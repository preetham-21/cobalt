       IDENTIFICATION DIVISION.
       PROGRAM-ID. PAYROLL.
      *****************************************************************
      * PAYROLL - BIWEEKLY PAYROLL REGISTER.
      *
      * INPUT : PAYIN  - HEADER, DETAIL..., TRAILER (PAYREC).
      * OUTPUT: PAYRPT - 80-COLUMN PAYROLL REGISTER.
      *
      * HOURLY  : FIRST 40 HOURS AT RATE, EXCESS AT 1.5 X RATE,
      *           GROSS ROUNDED TO THE CENT.
      * SALARIED: ANNUAL SALARY / 26, ROUNDED TO THE CENT.
      * TAX     : (GROSS - PRE-TAX DEDUCTION, FLOORED AT ZERO)
      *           X RATE FOR THE TAX CODE, TRUNCATED (NOT ROUNDED).
      * NET     : GROSS - DEDUCTION - TAX. MAY BE NEGATIVE (CR).
      * CONTROL : DETAIL COUNT AND EMP-ID HASH MUST MATCH TRAILER.
      *****************************************************************
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT PAY-FILE ASSIGN TO "PAYIN"
               ORGANIZATION IS LINE SEQUENTIAL
               FILE STATUS IS WS-PAY-STATUS.
           SELECT RPT-FILE ASSIGN TO "PAYRPT"
               ORGANIZATION IS LINE SEQUENTIAL
               FILE STATUS IS WS-RPT-STATUS.

       DATA DIVISION.
       FILE SECTION.
       FD  PAY-FILE.
           COPY PAYREC.

       FD  RPT-FILE.
       01  RPT-LINE                    PIC X(80).

       WORKING-STORAGE SECTION.
       01  WS-PAY-STATUS               PIC XX.
       01  WS-RPT-STATUS               PIC XX.
       01  WS-EOF-FLAG                 PIC X VALUE 'N'.
           88  WS-EOF                  VALUE 'Y'.
       01  WS-TRAILER-FLAG             PIC X VALUE 'N'.
           88  WS-TRAILER-SEEN         VALUE 'Y'.
       01  WS-VALID-FLAG               PIC X VALUE 'Y'.
           88  WS-VALID                VALUE 'Y'.
       01  WS-ERROR-MSG                PIC X(52).

       01  WS-PAY-FIELDS.
           05  WS-REG-HOURS            PIC 9(3)V99 COMP-3.
           05  WS-OT-HOURS             PIC 9(3)V99 COMP-3.
           05  WS-GROSS                PIC S9(7)V99 COMP-3.
           05  WS-TAXABLE              PIC S9(7)V99 COMP-3.
           05  WS-TAX                  PIC S9(7)V99 COMP-3.
           05  WS-NET                  PIC S9(7)V99 COMP-3.
           05  WS-TAX-RATE             PIC V999 COMP-3.

       01  WS-TOTALS.
           05  WS-DETAIL-COUNT         PIC 9(6) COMP-3 VALUE 0.
           05  WS-PAID-COUNT           PIC 9(6) COMP-3 VALUE 0.
           05  WS-ERROR-COUNT          PIC 9(6) COMP-3 VALUE 0.
           05  WS-UNKNOWN-COUNT        PIC 9(6) COMP-3 VALUE 0.
           05  WS-HASH-TOTAL           PIC 9(12) COMP-3 VALUE 0.
           05  WS-TOTAL-GROSS          PIC S9(9)V99 COMP-3 VALUE 0.
           05  WS-TOTAL-TAX            PIC S9(9)V99 COMP-3 VALUE 0.
           05  WS-TOTAL-NET            PIC S9(9)V99 COMP-3 VALUE 0.
       01  WS-EXPECTED-COUNT           PIC 9(6) VALUE 0.
       01  WS-EXPECTED-HASH            PIC 9(12) VALUE 0.

       01  WS-TITLE-LINE.
           05  FILLER                  PIC X(32) VALUE
               'PAYROLL REGISTER  PERIOD ENDING '.
           05  TL-PERIOD               PIC 9999/99/99.
           05  FILLER                  PIC X(2)  VALUE SPACES.
           05  TL-COMPANY              PIC X(30).
           05  FILLER                  PIC X(6)  VALUE SPACES.

       01  WS-COLUMN-LINE.
           05  FILLER                  PIC X(7)  VALUE 'EMP-ID'.
           05  FILLER                  PIC X(21) VALUE 'NAME'.
           05  FILLER                  PIC X(2)  VALUE 'T'.
           05  FILLER                  PIC X(15) VALUE
               '          GROSS'.
           05  FILLER                  PIC X(11) VALUE
               '        TAX'.
           05  FILLER                  PIC X(24) VALUE
               '           NET'.

       01  WS-DETAIL-LINE.
           05  DL-EMP-ID               PIC 9(6).
           05  FILLER                  PIC X     VALUE SPACE.
           05  DL-EMP-NAME             PIC X(20).
           05  FILLER                  PIC X     VALUE SPACE.
           05  DL-PAY-TYPE             PIC X.
           05  FILLER                  PIC X     VALUE SPACE.
           05  DL-GROSS                PIC $$$,$$$,$$9.99.
           05  FILLER                  PIC X     VALUE SPACE.
           05  DL-TAX                  PIC ZZZ,ZZ9.99.
           05  FILLER                  PIC X     VALUE SPACE.
           05  DL-NET                  PIC Z,ZZZ,ZZ9.99CR.
           05  FILLER                  PIC X(10) VALUE SPACES.

       01  WS-ERROR-LINE.
           05  EL-EMP-ID               PIC 9(6).
           05  FILLER                  PIC X     VALUE SPACE.
           05  EL-EMP-NAME             PIC X(20).
           05  FILLER                  PIC X     VALUE SPACE.
           05  EL-MESSAGE              PIC X(52).

       01  WS-TOTAL-LINE.
           05  TOT-LABEL               PIC X(30).
           05  TOT-AMOUNT              PIC $$$,$$$,$$$,$$9.99CR.
           05  FILLER                  PIC X(30) VALUE SPACES.

       01  WS-COUNT-LINE.
           05  CNT-LABEL               PIC X(30).
           05  CNT-VALUE               PIC ZZZ,ZZ9.
           05  FILLER                  PIC X(43) VALUE SPACES.

       PROCEDURE DIVISION.
       0000-MAIN.
           PERFORM 1000-INITIALIZE
           PERFORM 2000-PROCESS-RECORD UNTIL WS-EOF
           PERFORM 3000-FINALIZE
           STOP RUN.

       1000-INITIALIZE.
           OPEN INPUT PAY-FILE
           IF WS-PAY-STATUS NOT = '00'
               DISPLAY 'PAYROLL: OPEN PAYIN FAILED, STATUS '
                       WS-PAY-STATUS
               MOVE 16 TO RETURN-CODE
               STOP RUN
           END-IF
           OPEN OUTPUT RPT-FILE
           PERFORM 8000-READ-PAY
           IF WS-EOF OR NOT PAY-HEADER
               DISPLAY 'PAYROLL: FIRST RECORD IS NOT A HEADER'
               CLOSE PAY-FILE RPT-FILE
               MOVE 16 TO RETURN-CODE
               STOP RUN
           END-IF
           MOVE PAY-PERIOD-END TO TL-PERIOD
           MOVE PAY-COMPANY    TO TL-COMPANY
           WRITE RPT-LINE FROM WS-TITLE-LINE
           WRITE RPT-LINE FROM WS-COLUMN-LINE
           MOVE ALL '-' TO RPT-LINE
           WRITE RPT-LINE
           PERFORM 8000-READ-PAY.

       2000-PROCESS-RECORD.
           EVALUATE TRUE
               WHEN PAY-DETAIL
                   PERFORM 2100-PROCESS-EMPLOYEE
               WHEN PAY-TRAILER
                   SET WS-TRAILER-SEEN TO TRUE
                   MOVE PAY-EXPECTED-COUNT TO WS-EXPECTED-COUNT
                   MOVE PAY-EXPECTED-HASH  TO WS-EXPECTED-HASH
               WHEN OTHER
                   ADD 1 TO WS-UNKNOWN-COUNT
           END-EVALUATE
           PERFORM 8000-READ-PAY.

       2100-PROCESS-EMPLOYEE.
           ADD 1 TO WS-DETAIL-COUNT
           ADD EMP-ID TO WS-HASH-TOTAL
           PERFORM 2150-VALIDATE
           IF WS-VALID
               IF EMP-HOURLY
                   PERFORM 2200-CALC-HOURLY
               ELSE
                   COMPUTE WS-GROSS ROUNDED = EMP-RATE / 26
               END-IF
               PERFORM 2300-CALC-NET
               PERFORM 2400-WRITE-DETAIL
           ELSE
               PERFORM 2900-WRITE-ERROR
           END-IF.

       2150-VALIDATE.
           MOVE 'Y' TO WS-VALID-FLAG
           IF NOT EMP-HOURLY AND NOT EMP-SALARIED
               MOVE 'N' TO WS-VALID-FLAG
               MOVE 'INVALID PAY TYPE' TO WS-ERROR-MSG
           ELSE
               EVALUATE EMP-TAX-CODE
                   WHEN 'A'
                       MOVE .100 TO WS-TAX-RATE
                   WHEN 'B'
                       MOVE .220 TO WS-TAX-RATE
                   WHEN 'C'
                       MOVE .300 TO WS-TAX-RATE
                   WHEN 'X'
                       MOVE 0 TO WS-TAX-RATE
                   WHEN OTHER
                       MOVE 'N' TO WS-VALID-FLAG
                       MOVE 'INVALID TAX CODE' TO WS-ERROR-MSG
               END-EVALUATE
           END-IF.

       2200-CALC-HOURLY.
           IF EMP-HOURS > 40
               MOVE 40 TO WS-REG-HOURS
               COMPUTE WS-OT-HOURS = EMP-HOURS - 40
           ELSE
               MOVE EMP-HOURS TO WS-REG-HOURS
               MOVE 0 TO WS-OT-HOURS
           END-IF
           COMPUTE WS-GROSS ROUNDED =
               (WS-REG-HOURS * EMP-RATE)
             + (WS-OT-HOURS * EMP-RATE * 1.5).

       2300-CALC-NET.
           COMPUTE WS-TAXABLE = WS-GROSS - EMP-PRETAX-DED
           IF WS-TAXABLE < 0
               MOVE 0 TO WS-TAXABLE
           END-IF
      *    TRUNCATION IS DELIBERATE: NO ROUNDED PHRASE ON THE TAX.
           COMPUTE WS-TAX = WS-TAXABLE * WS-TAX-RATE
           COMPUTE WS-NET = WS-GROSS - EMP-PRETAX-DED - WS-TAX
           ADD 1        TO WS-PAID-COUNT
           ADD WS-GROSS TO WS-TOTAL-GROSS
           ADD WS-TAX   TO WS-TOTAL-TAX
           ADD WS-NET   TO WS-TOTAL-NET.

       2400-WRITE-DETAIL.
           MOVE EMP-ID       TO DL-EMP-ID
           MOVE EMP-NAME     TO DL-EMP-NAME
           MOVE EMP-PAY-TYPE TO DL-PAY-TYPE
           MOVE WS-GROSS     TO DL-GROSS
           MOVE WS-TAX       TO DL-TAX
           MOVE WS-NET       TO DL-NET
           WRITE RPT-LINE FROM WS-DETAIL-LINE.

       2900-WRITE-ERROR.
           ADD 1 TO WS-ERROR-COUNT
           MOVE EMP-ID       TO EL-EMP-ID
           MOVE EMP-NAME     TO EL-EMP-NAME
           MOVE WS-ERROR-MSG TO EL-MESSAGE
           WRITE RPT-LINE FROM WS-ERROR-LINE.

       3000-FINALIZE.
           MOVE ALL '-' TO RPT-LINE
           WRITE RPT-LINE
           MOVE 'TOTAL GROSS' TO TOT-LABEL
           MOVE WS-TOTAL-GROSS TO TOT-AMOUNT
           WRITE RPT-LINE FROM WS-TOTAL-LINE
           MOVE 'TOTAL TAX' TO TOT-LABEL
           MOVE WS-TOTAL-TAX TO TOT-AMOUNT
           WRITE RPT-LINE FROM WS-TOTAL-LINE
           MOVE 'TOTAL NET' TO TOT-LABEL
           MOVE WS-TOTAL-NET TO TOT-AMOUNT
           WRITE RPT-LINE FROM WS-TOTAL-LINE
           MOVE 'EMPLOYEES PAID' TO CNT-LABEL
           MOVE WS-PAID-COUNT TO CNT-VALUE
           WRITE RPT-LINE FROM WS-COUNT-LINE
           MOVE 'DETAIL RECORDS IN ERROR' TO CNT-LABEL
           MOVE WS-ERROR-COUNT TO CNT-VALUE
           WRITE RPT-LINE FROM WS-COUNT-LINE
           MOVE 'UNKNOWN RECORD TYPES' TO CNT-LABEL
           MOVE WS-UNKNOWN-COUNT TO CNT-VALUE
           WRITE RPT-LINE FROM WS-COUNT-LINE
           EVALUATE TRUE
               WHEN NOT WS-TRAILER-SEEN
                   MOVE 'CONTROL CHECK: TRAILER MISSING' TO RPT-LINE
                   MOVE 4 TO RETURN-CODE
               WHEN WS-DETAIL-COUNT = WS-EXPECTED-COUNT
                AND WS-HASH-TOTAL = WS-EXPECTED-HASH
                   MOVE 'CONTROL CHECK: OK' TO RPT-LINE
               WHEN OTHER
                   MOVE 'CONTROL CHECK: MISMATCH' TO RPT-LINE
                   MOVE 4 TO RETURN-CODE
           END-EVALUATE
           WRITE RPT-LINE
           CLOSE PAY-FILE RPT-FILE.

       8000-READ-PAY.
           READ PAY-FILE
               AT END
                   SET WS-EOF TO TRUE
           END-READ.
