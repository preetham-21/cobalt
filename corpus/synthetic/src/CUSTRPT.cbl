       IDENTIFICATION DIVISION.
       PROGRAM-ID. CUSTRPT.
      *****************************************************************
      * CUSTRPT - CUSTOMER BALANCE REPORT WITH REGION CONTROL BREAKS.
      *
      * INPUT : CUSTIN - CUSTOMER MASTER, SORTED BY REGION, CUST-ID.
      * OUTPUT: RPTOUT - 80-COLUMN PRINT REPORT.
      *
      * CLOSED CUSTOMERS ARE COUNTED BUT NOT PRINTED. A CUSTOMER WHOSE
      * BALANCE EXCEEDS THE CREDIT LIMIT IS FLAGGED 'OVER LIMIT'.
      *****************************************************************
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT CUST-FILE ASSIGN TO "CUSTIN"
               ORGANIZATION IS LINE SEQUENTIAL
               FILE STATUS IS WS-CUST-STATUS.
           SELECT RPT-FILE ASSIGN TO "RPTOUT"
               ORGANIZATION IS LINE SEQUENTIAL
               FILE STATUS IS WS-RPT-STATUS.

       DATA DIVISION.
       FILE SECTION.
       FD  CUST-FILE.
           COPY CUSTREC.

       FD  RPT-FILE.
       01  RPT-LINE                    PIC X(80).

       WORKING-STORAGE SECTION.
       01  WS-CUST-STATUS              PIC XX.
       01  WS-RPT-STATUS               PIC XX.
       01  WS-EOF-FLAG                 PIC X VALUE 'N'.
           88  WS-EOF                  VALUE 'Y'.
       01  WS-FIRST-FLAG               PIC X VALUE 'Y'.
           88  WS-FIRST-RECORD         VALUE 'Y'.
       01  WS-PREV-REGION              PIC X(2) VALUE SPACES.

       01  WS-COUNTERS.
           05  WS-READ-COUNT           PIC 9(6) COMP-3 VALUE 0.
           05  WS-PRINT-COUNT          PIC 9(6) COMP-3 VALUE 0.
           05  WS-CLOSED-COUNT         PIC 9(6) COMP-3 VALUE 0.
           05  WS-OVER-COUNT           PIC 9(6) COMP-3 VALUE 0.

       01  WS-TOTALS.
           05  WS-REGION-TOTAL         PIC S9(9)V99 COMP-3 VALUE 0.
           05  WS-GRAND-TOTAL          PIC S9(11)V99 COMP-3 VALUE 0.

       01  WS-HEADING-1.
           05  FILLER                  PIC X(20) VALUE 'CUSTRPT'.
           05  FILLER                  PIC X(40) VALUE
               'CUSTOMER BALANCE REPORT BY REGION'.
           05  FILLER                  PIC X(20) VALUE SPACES.

       01  WS-HEADING-2.
           05  FILLER                  PIC X(8)  VALUE 'CUST-ID'.
           05  FILLER                  PIC X(27) VALUE 'NAME'.
           05  FILLER                  PIC X(4)  VALUE 'RG'.
           05  FILLER                  PIC X(15) VALUE '      BALANCE'.
           05  FILLER                  PIC X(26) VALUE 'FLAG'.

       01  WS-DETAIL-LINE.
           05  DL-CUST-ID              PIC 9(6).
           05  FILLER                  PIC X(2)  VALUE SPACES.
           05  DL-NAME                 PIC X(25).
           05  FILLER                  PIC X(2)  VALUE SPACES.
           05  DL-REGION               PIC X(2).
           05  FILLER                  PIC X(2)  VALUE SPACES.
           05  DL-BALANCE              PIC -Z,ZZZ,ZZ9.99.
           05  FILLER                  PIC X(2)  VALUE SPACES.
           05  DL-FLAG                 PIC X(10).
           05  FILLER                  PIC X(16) VALUE SPACES.

       01  WS-REGION-LINE.
           05  FILLER                  PIC X(10) VALUE SPACES.
           05  FILLER                  PIC X(14) VALUE 'REGION TOTAL'.
           05  RL-REGION               PIC X(2).
           05  FILLER                  PIC X(11) VALUE SPACES.
           05  RL-TOTAL                PIC -ZZZ,ZZZ,ZZ9.99.
           05  FILLER                  PIC X(28) VALUE SPACES.

       01  WS-GRAND-LINE.
           05  FILLER                  PIC X(24)
                                       VALUE 'GRAND TOTAL BALANCE'.
           05  FILLER                  PIC X(10) VALUE SPACES.
           05  GL-TOTAL                PIC -ZZ,ZZZ,ZZZ,ZZ9.99.
           05  FILLER                  PIC X(28) VALUE SPACES.

       01  WS-COUNT-LINE.
           05  CL-LABEL                PIC X(30).
           05  CL-COUNT                PIC ZZZ,ZZ9.
           05  FILLER                  PIC X(43) VALUE SPACES.

       PROCEDURE DIVISION.
       0000-MAIN.
           PERFORM 1000-INITIALIZE
           PERFORM 2000-PROCESS-RECORD UNTIL WS-EOF
           PERFORM 3000-FINALIZE
           STOP RUN.

       1000-INITIALIZE.
           OPEN INPUT CUST-FILE
           IF WS-CUST-STATUS NOT = '00'
               DISPLAY 'CUSTRPT: OPEN CUSTIN FAILED, STATUS '
                       WS-CUST-STATUS
               MOVE 16 TO RETURN-CODE
               STOP RUN
           END-IF
           OPEN OUTPUT RPT-FILE
           WRITE RPT-LINE FROM WS-HEADING-1
           WRITE RPT-LINE FROM WS-HEADING-2
           MOVE ALL '-' TO RPT-LINE
           WRITE RPT-LINE
           PERFORM 8000-READ-CUSTOMER.

       2000-PROCESS-RECORD.
           IF CUST-CLOSED
               ADD 1 TO WS-CLOSED-COUNT
           ELSE
               IF NOT WS-FIRST-RECORD
                  AND CUST-REGION NOT = WS-PREV-REGION
                   PERFORM 2500-REGION-BREAK
               END-IF
               MOVE 'N' TO WS-FIRST-FLAG
               MOVE CUST-REGION TO WS-PREV-REGION
               PERFORM 2100-PRINT-DETAIL
           END-IF
           PERFORM 8000-READ-CUSTOMER.

       2100-PRINT-DETAIL.
           MOVE CUST-ID      TO DL-CUST-ID
           MOVE CUST-NAME    TO DL-NAME
           MOVE CUST-REGION  TO DL-REGION
           MOVE CUST-BALANCE TO DL-BALANCE
           IF CUST-BALANCE > CUST-CREDIT-LIMIT
               MOVE 'OVER LIMIT' TO DL-FLAG
               ADD 1 TO WS-OVER-COUNT
           ELSE
               MOVE SPACES TO DL-FLAG
           END-IF
           WRITE RPT-LINE FROM WS-DETAIL-LINE
           ADD 1 TO WS-PRINT-COUNT
           ADD CUST-BALANCE TO WS-REGION-TOTAL
           ADD CUST-BALANCE TO WS-GRAND-TOTAL.

       2500-REGION-BREAK.
           MOVE WS-PREV-REGION  TO RL-REGION
           MOVE WS-REGION-TOTAL TO RL-TOTAL
           WRITE RPT-LINE FROM WS-REGION-LINE
           MOVE SPACES TO RPT-LINE
           WRITE RPT-LINE
           MOVE 0 TO WS-REGION-TOTAL.

       3000-FINALIZE.
           IF NOT WS-FIRST-RECORD
               PERFORM 2500-REGION-BREAK
           END-IF
           MOVE WS-GRAND-TOTAL TO GL-TOTAL
           WRITE RPT-LINE FROM WS-GRAND-LINE
           MOVE 'RECORDS READ' TO CL-LABEL
           MOVE WS-READ-COUNT TO CL-COUNT
           WRITE RPT-LINE FROM WS-COUNT-LINE
           MOVE 'CUSTOMERS PRINTED' TO CL-LABEL
           MOVE WS-PRINT-COUNT TO CL-COUNT
           WRITE RPT-LINE FROM WS-COUNT-LINE
           MOVE 'CLOSED ACCOUNTS SKIPPED' TO CL-LABEL
           MOVE WS-CLOSED-COUNT TO CL-COUNT
           WRITE RPT-LINE FROM WS-COUNT-LINE
           MOVE 'OVER CREDIT LIMIT' TO CL-LABEL
           MOVE WS-OVER-COUNT TO CL-COUNT
           WRITE RPT-LINE FROM WS-COUNT-LINE
           CLOSE CUST-FILE RPT-FILE.

       8000-READ-CUSTOMER.
           READ CUST-FILE
               AT END
                   SET WS-EOF TO TRUE
               NOT AT END
                   ADD 1 TO WS-READ-COUNT
           END-READ.
