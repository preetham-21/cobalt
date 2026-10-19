package cobalt.migrated.support;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.Charset;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Iterator;
import java.util.NoSuchElementException;

/**
 * LINE SEQUENTIAL files: one record per line, ISO-8859-1, records padded
 * to the record length on read and trailing spaces trimmed on write (as
 * GnuCOBOL does).
 */
public final class LineFiles {
    private LineFiles() {}

    public static final Charset CHARSET = StandardCharsets.ISO_8859_1;

    /** Sequential reader of fixed-length records. */
    public static final class Reader implements AutoCloseable, Iterator<String> {
        private final BufferedReader in;
        private final int recordLength;
        private String next;

        public Reader(Path path, int recordLength) throws IOException {
            this.in = Files.newBufferedReader(path, CHARSET);
            this.recordLength = recordLength;
            this.next = in.readLine();
        }

        @Override
        public boolean hasNext() {
            return next != null;
        }

        @Override
        public String next() {
            if (next == null) {
                throw new NoSuchElementException();
            }
            String record = Pic.alpha(next, recordLength);
            try {
                next = in.readLine();
            } catch (IOException e) {
                throw new java.io.UncheckedIOException(e);
            }
            return record;
        }

        @Override
        public void close() throws IOException {
            in.close();
        }
    }

    /** Sequential writer; trims trailing spaces like LINE SEQUENTIAL WRITE. */
    public static final class Writer implements AutoCloseable {
        private final BufferedWriter out;

        public Writer(Path path) throws IOException {
            this.out = Files.newBufferedWriter(path, CHARSET);
        }

        public void write(String record) throws IOException {
            out.write(record.stripTrailing());
            out.write('\n');
        }

        @Override
        public void close() throws IOException {
            out.close();
        }
    }
}
