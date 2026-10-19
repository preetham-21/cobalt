       IDENTIFICATION DIVISION.
       PROGRAM-ID. RATECALC.
      *****************************************************************
      * RATECALC - TIERED INTEREST RATE AND MONTHLY INTEREST.
      *
      * CALLED SUBPROGRAM (NO FILES). GIVEN AN ACCOUNT TYPE AND A
      * BALANCE, RETURNS THE ANNUAL RATE AND THE MONTHLY INTEREST,
      * ROUNDED TO THE CENT.
      *
      * RESULT CODE: 00 = OK, 08 = UNKNOWN ACCOUNT TYPE (RATE 0).
      * NEGATIVE DEPOSIT BALANCES EARN NO INTEREST.
      *****************************************************************
       DATA DIVISION.
       LINKAGE SECTION.
       01  LK-ACCT-TYPE                PIC X.
       01  LK-BALANCE                  PIC S9(9)V99 COMP-3.
       01  LK-ANNUAL-RATE              PIC 9V9(6) COMP-3.
       01  LK-MONTHLY-INTEREST         PIC S9(9)V99 COMP-3.
       01  LK-RESULT-CODE              PIC 9(2).

       PROCEDURE DIVISION USING LK-ACCT-TYPE
                                LK-BALANCE
                                LK-ANNUAL-RATE
                                LK-MONTHLY-INTEREST
                                LK-RESULT-CODE.
       0000-MAIN.
           MOVE 0 TO LK-RESULT-CODE
           EVALUATE LK-ACCT-TYPE
               WHEN 'S'
                   PERFORM 1000-SAVINGS-TIER
               WHEN 'C'
                   MOVE 0.0005 TO LK-ANNUAL-RATE
               WHEN 'L'
                   MOVE 0.0699 TO LK-ANNUAL-RATE
               WHEN OTHER
                   MOVE 0 TO LK-ANNUAL-RATE
                   MOVE 8 TO LK-RESULT-CODE
           END-EVALUATE
           IF LK-BALANCE < 0 AND LK-ACCT-TYPE NOT = 'L'
               MOVE 0 TO LK-ANNUAL-RATE
           END-IF
           COMPUTE LK-MONTHLY-INTEREST ROUNDED =
               LK-BALANCE * LK-ANNUAL-RATE / 12
           GOBACK.

       1000-SAVINGS-TIER.
           EVALUATE TRUE
               WHEN LK-BALANCE < 1000
                   MOVE 0.0050 TO LK-ANNUAL-RATE
               WHEN LK-BALANCE < 50000
                   MOVE 0.0125 TO LK-ANNUAL-RATE
               WHEN OTHER
                   MOVE 0.0215 TO LK-ANNUAL-RATE
           END-EVALUATE.
