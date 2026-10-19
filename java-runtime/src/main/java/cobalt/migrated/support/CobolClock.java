package cobalt.migrated.support;

import java.time.LocalDateTime;
import java.time.ZoneOffset;

/**
 * The run's clock. Equivalence runs pin "now" (GnuCOBOL: COB_CURRENT_DATE;
 * Java: the COBALT_CURRENT_DATE environment variable, ISO-8601 local time) so
 * programs that stamp records with the current time stay comparable.
 */
public final class CobolClock {
    private CobolClock() {}

    /** The pinned time if set, else the real UTC time. */
    public static LocalDateTime now() {
        String fixed = System.getenv("COBALT_CURRENT_DATE");
        if (fixed != null && !fixed.isBlank()) {
            return LocalDateTime.parse(fixed.trim());
        }
        return LocalDateTime.now(ZoneOffset.UTC);
    }

    /** FUNCTION CURRENT-DATE: {@code YYYYMMDDhhmmsscc+0000} (21 characters, UTC). */
    public static String currentDate() {
        LocalDateTime t = now();
        return String.format("%04d%02d%02d%02d%02d%02d%02d+0000",
                t.getYear(), t.getMonthValue(), t.getDayOfMonth(),
                t.getHour(), t.getMinute(), t.getSecond(), t.getNano() / 10_000_000);
    }
}
