package cobalt.migrated.support;

import java.math.BigDecimal;
import java.math.BigInteger;
import java.math.RoundingMode;

/**
 * COBOL data semantics for migrated programs: fixed-width DISPLAY fields,
 * MOVE truncation, ROUNDED, and numeric-edited PICTURE formatting.
 *
 * <p>All money is {@link BigDecimal}. Nothing here rounds unless asked to:
 * COBOL truncates by default, so {@link #truncate} is the default store.
 */
public final class Pic {
    private Pic() {}

    /** Extra fraction digits kept on division, like GnuCOBOL's intermediates. */
    public static final int DIVISION_SCALE = 40;

    // ------------------------------------------------------------------ //
    // Parsing DISPLAY fields
    // ------------------------------------------------------------------ //

    /** {@code PIC 9(n)V9(scale)}: unsigned digits with an implied point. */
    public static BigDecimal unsigned(String digits, int scale) {
        String clean = digits.replace(' ', '0');
        return new BigDecimal(new BigInteger(clean.isEmpty() ? "0" : clean), scale);
    }

    /** {@code PIC S9(n)V9(scale) SIGN LEADING SEPARATE}: sign char then digits. */
    public static BigDecimal signedLeading(String field, int scale) {
        BigDecimal magnitude = unsigned(field.substring(1), scale);
        return field.charAt(0) == '-' ? magnitude.negate() : magnitude;
    }

    /** Integer value of {@code PIC 9(n)}. */
    public static long integer(String digits) {
        String clean = digits.trim();
        return clean.isEmpty() ? 0L : Long.parseLong(clean);
    }

    // ------------------------------------------------------------------ //
    // Storing into numeric fields
    // ------------------------------------------------------------------ //

    /**
     * MOVE semantics: keep {@code scale} fraction digits (truncating toward
     * zero) and the low-order {@code intDigits} integer digits.
     */
    public static BigDecimal truncate(BigDecimal value, int intDigits, int scale) {
        BigDecimal scaled = value.setScale(scale, RoundingMode.DOWN);
        BigInteger unscaled = scaled.unscaledValue();
        BigInteger limit = BigInteger.TEN.pow(intDigits + scale);
        BigInteger kept = unscaled.abs().mod(limit);
        return new BigDecimal(unscaled.signum() < 0 ? kept.negate() : kept, scale);
    }

    /** ROUNDED (default mode: half away from zero) to {@code scale} digits. */
    public static BigDecimal rounded(BigDecimal value, int scale) {
        return value.setScale(scale, RoundingMode.HALF_UP);
    }

    /** Division with GnuCOBOL-like intermediate precision. */
    public static BigDecimal divide(BigDecimal dividend, BigDecimal divisor) {
        return dividend.divide(divisor, DIVISION_SCALE, RoundingMode.DOWN);
    }

    /** True if {@code value} does not fit {@code S9(intDigits)V9(scale)} (SIZE ERROR). */
    public static boolean sizeError(BigDecimal value, int intDigits, int scale) {
        BigDecimal limit = BigDecimal.TEN.pow(intDigits);
        return value.setScale(scale, RoundingMode.DOWN).abs().compareTo(limit) >= 0;
    }

    // ------------------------------------------------------------------ //
    // Formatting DISPLAY fields
    // ------------------------------------------------------------------ //

    /** {@code PIC 9(intDigits)V9(scale)} as stored characters (no sign). */
    public static String digits(BigDecimal value, int intDigits, int scale) {
        BigDecimal v = truncate(value, intDigits, scale).abs();
        String s = v.unscaledValue().toString();
        int width = intDigits + scale;
        return "0".repeat(Math.max(0, width - s.length())) + s;
    }

    /** {@code PIC S9(intDigits)V9(scale) SIGN LEADING SEPARATE}. */
    public static String signedLeading(BigDecimal value, int intDigits, int scale) {
        BigDecimal v = truncate(value, intDigits, scale);
        return (v.signum() < 0 ? "-" : "+") + digits(v, intDigits, scale);
    }

    /** Alphanumeric MOVE: left-justify, pad with spaces, truncate on the right. */
    public static String alpha(String value, int width) {
        if (value.length() >= width) {
            return value.substring(0, width);
        }
        return value + " ".repeat(width - value.length());
    }

    // ------------------------------------------------------------------ //
    // Numeric-edited pictures
    // ------------------------------------------------------------------ //

    /**
     * Format {@code value} for a numeric-edited PICTURE such as
     * {@code -Z,ZZZ,ZZ9.99}, {@code $$$,$$9.99}, {@code ZZ9.99CR} or
     * {@code 9999/99/99}. Supports 9 Z * , . / B 0, fixed and floating
     * $ + -, and trailing CR / DB. Excess integer digits are truncated
     * (MOVE semantics); fraction digits are truncated.
     */
    public static String edit(BigDecimal value, String picture) {
        String pic = expand(picture);
        boolean negative = value.signum() < 0;
        int point = pic.indexOf('.');
        int intEnd = point >= 0 ? point : pic.length();

        // floating insertion symbol: $, + or - appearing more than once
        char floating = 0;
        for (char c : new char[] {'$', '+', '-'}) {
            if (count(pic, c) > 1) {
                floating = c;
                break;
            }
        }
        int floatStart = floating == 0 ? -1 : pic.indexOf(floating);
        int floatEnd = floating == 0 ? -1 : pic.lastIndexOf(floating, intEnd - 1);

        int intDigits = 0;
        int fracDigits = 0;
        for (int i = 0; i < pic.length(); i++) {
            if (isDigitPos(pic, i, floating, floatStart)) {
                if (i < intEnd) intDigits++; else fracDigits++;
            }
        }
        String digits = digits(value, intDigits, fracDigits);
        boolean allZero = digits.chars().allMatch(c -> c == '0');
        boolean star = pic.indexOf('*') >= 0;
        boolean hasNine = pic.indexOf('9') >= 0;

        if (allZero && !hasNine && floating == 0 && !star && pic.indexOf('Z') >= 0
                && pic.replaceAll("[Z,./B0]", "").isEmpty()) {
            return " ".repeat(lengthOf(pic));
        }

        StringBuilder out = new StringBuilder();
        int d = 0;
        boolean significant = false;
        int firstSig = -1;
        char fill = star ? '*' : ' ';
        for (int i = 0; i < pic.length(); i++) {
            char c = pic.charAt(i);
            if (i == point) {
                out.append('.');
                significant = true;
                continue;
            }
            if (isDigitPos(pic, i, floating, floatStart)) {
                char digit = digits.charAt(d++);
                boolean suppressible = c == 'Z' || c == '*' || (c == floating && i >= floatStart);
                if (i > intEnd || c == '9' || !suppressible || significant || digit != '0') {
                    if (!significant && i < intEnd) {
                        firstSig = out.length();
                    }
                    significant = true;
                    out.append(digit);
                } else {
                    out.append(fill);
                }
                continue;
            }
            if (i == floatStart && floating != 0) {
                out.append(' ');  // placeholder; the symbol is placed below
                continue;
            }
            switch (c) {
                case ',', '0', '/' -> out.append(significant ? c : fill);
                case 'B' -> out.append(significant ? ' ' : fill);
                case '+' -> out.append(negative ? '-' : '+');
                case '-' -> out.append(negative ? '-' : ' ');
                case '$' -> out.append('$');
                case 'C' -> out.append(negative ? "CR" : "  ");
                case 'D' -> out.append(negative ? "DB" : "  ");
                default -> out.append(c);
            }
        }

        if (floating != 0) {
            int limit = firstSig >= 0 ? firstSig : floatEnd + 1;
            int pos = Math.min(limit, floatEnd + 1) - 1;
            char symbol = switch (floating) {
                case '$' -> '$';
                case '+' -> negative ? '-' : '+';
                default -> negative ? '-' : ' ';
            };
            out.setCharAt(Math.max(pos, floatStart), symbol);
        }
        return out.toString();
    }

    private static boolean isDigitPos(String pic, int i, char floating, int floatStart) {
        char c = pic.charAt(i);
        if (c == '9' || c == 'Z' || c == '*') {
            return true;
        }
        return floating != 0 && c == floating && i > floatStart;
    }

    private static int count(String s, char c) {
        int n = 0;
        int point = s.indexOf('.');
        int end = point >= 0 ? point : s.length();
        for (int i = 0; i < end; i++) {
            if (s.charAt(i) == c) n++;
        }
        return n;
    }

    private static int lengthOf(String pic) {
        int n = 0;
        for (char c : pic.toCharArray()) {
            n += (c == 'C' || c == 'D') ? 2 : 1;
        }
        return n;
    }

    /** Expand repeats and tokenise CR/DB as single symbols 'C'/'D'. */
    private static String expand(String picture) {
        StringBuilder sb = new StringBuilder();
        String p = picture.toUpperCase();
        for (int i = 0; i < p.length(); i++) {
            char c = p.charAt(i);
            if (i + 1 < p.length() && p.charAt(i + 1) == '(') {
                int close = p.indexOf(')', i);
                int n = Integer.parseInt(p.substring(i + 2, close));
                sb.append(String.valueOf(c).repeat(n));
                i = close;
            } else if (c == 'C' && i + 1 < p.length() && p.charAt(i + 1) == 'R') {
                sb.append('C');
                i++;
            } else if (c == 'D' && i + 1 < p.length() && p.charAt(i + 1) == 'B') {
                sb.append('D');
                i++;
            } else {
                sb.append(c);
            }
        }
        return sb.toString();
    }
}
