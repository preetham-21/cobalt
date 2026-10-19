       IDENTIFICATION DIVISION.
       PROGRAM-ID. INTCALC.
      *****************************************************************
      * INTCALC - MONTH-END INTEREST ACCRUAL.
      *
      * INPUT : ACCTIN - ACCOUNT MASTER (ACCTREC).
      * OUTPUT: INTOUT - ONE RESULT RECORD PER ACCOUNT + A TRAILER.
      * CALLS : RATECALC.
      *
      * ACCOUNTS OPENED IN THE PROCESSING MONTH EARN NO INTEREST
      * (RESULT 04). IF ADDING INTEREST WOULD OVERFLOW THE BALANCE
      * FIELD THE BALANCE IS LEFT UNCHANGED (RESULT 12).
      *****************************************************************
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT ACCT-FILE ASSIGN TO "ACCTIN"
               ORGANIZATION IS LINE SEQUENTIAL
               FILE STATUS IS WS-ACCT-STATUS.
           SELECT INT-FILE ASSIGN TO "INTOUT"
               ORGANIZATION IS LINE SEQUENTIAL
               FILE STATUS IS WS-INT-STATUS.

       DATA DIVISION.
       FILE SECTION.
       FD  ACCT-FILE.
           COPY ACCTREC.

       FD  INT-FILE.
       01  INT-RECORD.
           05  INT-ACCT-ID             PIC 9(8).
           05  INT-ACCT-TYPE           PIC X.
           05  INT-OLD-BALANCE         PIC S9(9)V99
                                       SIGN LEADING SEPARATE.
           05  INT-RATE                PIC 9V9(6).
           05  INT-INTEREST            PIC S9(9)V99
                                       SIGN LEADING SEPARATE.
           05  INT-NEW-BALANCE         PIC S9(9)V99
                                       SIGN LEADING SEPARATE.
           05  INT-RESULT-CODE         PIC 9(2).
       01  INT-TRAILER.
           05  INT-TRL-TAG             PIC X(8).
           05  INT-TRL-COUNT           PIC 9(6).
           05  INT-TRL-TOTAL-INT       PIC S9(11)V99
                                       SIGN LEADING SEPARATE.
           05  INT-TRL-ERRORS          PIC 9(6).
           05  FILLER                  PIC X(20).

       WORKING-STORAGE SECTION.
       01  WS-ACCT-STATUS              PIC XX.
       01  WS-INT-STATUS               PIC XX.
       01  WS-EOF-FLAG                 PIC X VALUE 'N'.
           88  WS-EOF                  VALUE 'Y'.

      *    FIXED PROCESSING DATE KEEPS RUNS REPRODUCIBLE.
       01  WS-RUN-DATE                 PIC 9(8) VALUE 20261031.
       01  WS-RUN-DATE-R REDEFINES WS-RUN-DATE.
           05  WS-RUN-YYYY             PIC 9(4).
           05  WS-RUN-MM               PIC 9(2).
           05  WS-RUN-DD               PIC 9(2).

       01  WS-CALC-FIELDS.
           05  WS-BALANCE              PIC S9(9)V99 COMP-3.
           05  WS-RATE                 PIC 9V9(6) COMP-3.
           05  WS-INTEREST             PIC S9(9)V99 COMP-3.
           05  WS-NEW-BALANCE          PIC S9(9)V99 COMP-3.
           05  WS-RC                   PIC 9(2).

       01  WS-TOTALS.
           05  WS-REC-COUNT            PIC 9(6) COMP-3 VALUE 0.
           05  WS-ERROR-COUNT          PIC 9(6) COMP-3 VALUE 0.
           05  WS-TOTAL-INTEREST       PIC S9(11)V99 COMP-3 VALUE 0.

       PROCEDURE DIVISION.
       0000-MAIN.
           OPEN INPUT ACCT-FILE
           IF WS-ACCT-STATUS NOT = '00'
               DISPLAY 'INTCALC: OPEN ACCTIN FAILED, STATUS '
                       WS-ACCT-STATUS
               MOVE 16 TO RETURN-CODE
               STOP RUN
           END-IF
           OPEN OUTPUT INT-FILE
           PERFORM 8000-READ-ACCOUNT
           PERFORM 2000-PROCESS-ACCOUNT UNTIL WS-EOF
           PERFORM 3000-WRITE-TRAILER
           CLOSE ACCT-FILE INT-FILE
           STOP RUN.

       2000-PROCESS-ACCOUNT.
           ADD 1 TO WS-REC-COUNT
           MOVE ACCT-BALANCE TO WS-BALANCE
           MOVE 0 TO WS-RC
           IF ACCT-OPEN-YYYY = WS-RUN-YYYY
              AND ACCT-OPEN-MM = WS-RUN-MM
               MOVE 0 TO WS-RATE
               MOVE 0 TO WS-INTEREST
               MOVE 4 TO WS-RC
           ELSE
               CALL 'RATECALC' USING ACCT-TYPE
                                     WS-BALANCE
                                     WS-RATE
                                     WS-INTEREST
                                     WS-RC
           END-IF
           COMPUTE WS-NEW-BALANCE = WS-BALANCE + WS-INTEREST
               ON SIZE ERROR
                   MOVE WS-BALANCE TO WS-NEW-BALANCE
                   MOVE 0 TO WS-INTEREST
                   MOVE 12 TO WS-RC
           END-COMPUTE
           IF WS-RC >= 8
               ADD 1 TO WS-ERROR-COUNT
           END-IF
           ADD WS-INTEREST TO WS-TOTAL-INTEREST
           PERFORM 2100-WRITE-RESULT
           PERFORM 8000-READ-ACCOUNT.

       2100-WRITE-RESULT.
           MOVE ACCT-ID          TO INT-ACCT-ID
           MOVE ACCT-TYPE        TO INT-ACCT-TYPE
           MOVE WS-BALANCE       TO INT-OLD-BALANCE
           MOVE WS-RATE          TO INT-RATE
           MOVE WS-INTEREST      TO INT-INTEREST
           MOVE WS-NEW-BALANCE   TO INT-NEW-BALANCE
           MOVE WS-RC            TO INT-RESULT-CODE
           WRITE INT-RECORD.

       3000-WRITE-TRAILER.
           MOVE SPACES             TO INT-TRAILER
           MOVE 'TRAILER '         TO INT-TRL-TAG
           MOVE WS-REC-COUNT       TO INT-TRL-COUNT
           MOVE WS-TOTAL-INTEREST  TO INT-TRL-TOTAL-INT
           MOVE WS-ERROR-COUNT     TO INT-TRL-ERRORS
           WRITE INT-TRAILER.

       8000-READ-ACCOUNT.
           READ ACCT-FILE
               AT END
                   SET WS-EOF TO TRUE
           END-READ.
