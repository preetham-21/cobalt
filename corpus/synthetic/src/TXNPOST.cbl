       IDENTIFICATION DIVISION.
       PROGRAM-ID. TXNPOST.
      *****************************************************************
      * TXNPOST - DAILY TRANSACTION POSTING (BALANCE-LINE MATCH/MERGE).
      *
      * INPUT : ACCTIN  - ACCOUNT MASTER, SORTED BY ACCT-ID.
      *         TXNIN   - TRANSACTIONS, SORTED BY ACCT-ID, SEQ.
      * OUTPUT: ACCTNEW - UPDATED ACCOUNT MASTER (SAME LAYOUT).
      *         TXNERR  - REJECTED TRANSACTIONS WITH A REASON.
      *         SYSOUT  - CONTROL TOTALS (DISPLAY).
      *
      * RULES : DP ADDS (FOR LOANS A DEPOSIT IS A REPAYMENT AND
      *         REDUCES THE BALANCE). WD SUBTRACTS IF FUNDS ALLOW AND
      *         IS NOT ALLOWED ON LOANS. FE ALWAYS SUBTRACTS AND MAY
      *         DRIVE THE BALANCE NEGATIVE. CLOSED ACCOUNTS ACCEPT NO
      *         TRANSACTIONS. AN OVERFLOWING UPDATE IS REJECTED.
      *****************************************************************
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT MASTER-FILE ASSIGN TO "ACCTIN"
               ORGANIZATION IS LINE SEQUENTIAL
               FILE STATUS IS WS-MASTER-STATUS.
           SELECT TXN-FILE ASSIGN TO "TXNIN"
               ORGANIZATION IS LINE SEQUENTIAL
               FILE STATUS IS WS-TXN-STATUS.
           SELECT NEW-MASTER-FILE ASSIGN TO "ACCTNEW"
               ORGANIZATION IS LINE SEQUENTIAL
               FILE STATUS IS WS-NEW-STATUS.
           SELECT ERR-FILE ASSIGN TO "TXNERR"
               ORGANIZATION IS LINE SEQUENTIAL
               FILE STATUS IS WS-ERR-STATUS.

       DATA DIVISION.
       FILE SECTION.
       FD  MASTER-FILE.
           COPY ACCTREC.

       FD  TXN-FILE.
           COPY TXNREC.

       FD  NEW-MASTER-FILE.
       01  NEW-MASTER-REC              PIC X(40).

       FD  ERR-FILE.
       01  ERR-RECORD.
           05  ERR-TXN-DATA            PIC X(40).
           05  ERR-REASON              PIC X(20).
           05  FILLER                  PIC X(20).

       WORKING-STORAGE SECTION.
       01  WS-MASTER-STATUS            PIC XX.
       01  WS-TXN-STATUS               PIC XX.
       01  WS-NEW-STATUS               PIC XX.
       01  WS-ERR-STATUS               PIC XX.

       01  WS-MASTER-KEY               PIC X(8).
       01  WS-TXN-KEY                  PIC X(8).
       01  WS-WORK-BALANCE             PIC S9(9)V99 COMP-3.
       01  WS-REASON                   PIC X(20).

       01  WS-COUNTERS.
           05  WS-MASTER-READ          PIC 9(6) VALUE 0.
           05  WS-MASTER-WRITTEN       PIC 9(6) VALUE 0.
           05  WS-TXN-READ             PIC 9(6) VALUE 0.
           05  WS-APPLIED-COUNT        PIC 9(6) VALUE 0.
           05  WS-REJECTED-COUNT       PIC 9(6) VALUE 0.
       01  WS-EDIT-COUNT               PIC ZZZ,ZZ9.

       PROCEDURE DIVISION.
       0000-MAIN.
           PERFORM 1000-OPEN-FILES
           PERFORM 8100-READ-MASTER
           PERFORM 8200-READ-TXN
           PERFORM 2000-MERGE
               UNTIL WS-MASTER-KEY = HIGH-VALUES
                 AND WS-TXN-KEY = HIGH-VALUES
           PERFORM 9000-CLOSE-FILES
           STOP RUN.

       1000-OPEN-FILES.
           OPEN INPUT MASTER-FILE
           IF WS-MASTER-STATUS NOT = '00'
               DISPLAY 'TXNPOST: OPEN ACCTIN FAILED, STATUS '
                       WS-MASTER-STATUS
               MOVE 16 TO RETURN-CODE
               STOP RUN
           END-IF
           OPEN INPUT TXN-FILE
           IF WS-TXN-STATUS NOT = '00'
               DISPLAY 'TXNPOST: OPEN TXNIN FAILED, STATUS '
                       WS-TXN-STATUS
               MOVE 16 TO RETURN-CODE
               STOP RUN
           END-IF
           OPEN OUTPUT NEW-MASTER-FILE
           OPEN OUTPUT ERR-FILE.

       2000-MERGE.
           EVALUATE TRUE
               WHEN WS-TXN-KEY < WS-MASTER-KEY
                   MOVE 'NO MATCHING ACCOUNT' TO WS-REASON
                   PERFORM 2900-REJECT-TXN
                   PERFORM 8200-READ-TXN
               WHEN WS-TXN-KEY = WS-MASTER-KEY
                   PERFORM 2100-APPLY-TXN
                   PERFORM 8200-READ-TXN
               WHEN OTHER
                   PERFORM 2800-WRITE-MASTER
                   PERFORM 8100-READ-MASTER
           END-EVALUATE.

       2100-APPLY-TXN.
           MOVE SPACES TO WS-REASON
           IF ACCT-CLOSED
               MOVE 'ACCOUNT CLOSED' TO WS-REASON
           ELSE
               EVALUATE TRUE
                   WHEN TXN-DEPOSIT AND ACCT-LOAN
                       SUBTRACT TXN-AMOUNT FROM WS-WORK-BALANCE
                           ON SIZE ERROR
                               MOVE 'BALANCE OVERFLOW' TO WS-REASON
                       END-SUBTRACT
                   WHEN TXN-DEPOSIT
                       ADD TXN-AMOUNT TO WS-WORK-BALANCE
                           ON SIZE ERROR
                               MOVE 'BALANCE OVERFLOW' TO WS-REASON
                       END-ADD
                   WHEN TXN-WITHDRAWAL AND ACCT-LOAN
                       MOVE 'INVALID FOR LOAN' TO WS-REASON
                   WHEN TXN-WITHDRAWAL
                       IF TXN-AMOUNT > WS-WORK-BALANCE
                           MOVE 'INSUFFICIENT FUNDS' TO WS-REASON
                       ELSE
                           SUBTRACT TXN-AMOUNT FROM WS-WORK-BALANCE
                       END-IF
                   WHEN TXN-FEE
                       SUBTRACT TXN-AMOUNT FROM WS-WORK-BALANCE
                           ON SIZE ERROR
                               MOVE 'BALANCE OVERFLOW' TO WS-REASON
                       END-SUBTRACT
                   WHEN OTHER
                       MOVE 'INVALID TXN CODE' TO WS-REASON
               END-EVALUATE
           END-IF
           IF WS-REASON = SPACES
               ADD 1 TO WS-APPLIED-COUNT
           ELSE
               PERFORM 2900-REJECT-TXN
           END-IF.

       2800-WRITE-MASTER.
           MOVE WS-WORK-BALANCE TO ACCT-BALANCE
           WRITE NEW-MASTER-REC FROM ACCOUNT-RECORD
           ADD 1 TO WS-MASTER-WRITTEN.

       2900-REJECT-TXN.
           MOVE SPACES             TO ERR-RECORD
           MOVE TRANSACTION-RECORD TO ERR-TXN-DATA
           MOVE WS-REASON          TO ERR-REASON
           WRITE ERR-RECORD
           ADD 1 TO WS-REJECTED-COUNT.

       8100-READ-MASTER.
           READ MASTER-FILE
               AT END
                   MOVE HIGH-VALUES TO WS-MASTER-KEY
               NOT AT END
                   ADD 1 TO WS-MASTER-READ
                   MOVE ACCT-ID TO WS-MASTER-KEY
                   MOVE ACCT-BALANCE TO WS-WORK-BALANCE
           END-READ.

       8200-READ-TXN.
           READ TXN-FILE
               AT END
                   MOVE HIGH-VALUES TO WS-TXN-KEY
               NOT AT END
                   ADD 1 TO WS-TXN-READ
                   MOVE TXN-ACCT-ID TO WS-TXN-KEY
           END-READ.

       9000-CLOSE-FILES.
           CLOSE MASTER-FILE TXN-FILE NEW-MASTER-FILE ERR-FILE
           MOVE WS-MASTER-READ TO WS-EDIT-COUNT
           DISPLAY 'TXNPOST MASTERS READ      ' WS-EDIT-COUNT
           MOVE WS-MASTER-WRITTEN TO WS-EDIT-COUNT
           DISPLAY 'TXNPOST MASTERS WRITTEN   ' WS-EDIT-COUNT
           MOVE WS-TXN-READ TO WS-EDIT-COUNT
           DISPLAY 'TXNPOST TRANSACTIONS READ ' WS-EDIT-COUNT
           MOVE WS-APPLIED-COUNT TO WS-EDIT-COUNT
           DISPLAY 'TXNPOST APPLIED           ' WS-EDIT-COUNT
           MOVE WS-REJECTED-COUNT TO WS-EDIT-COUNT
           DISPLAY 'TXNPOST REJECTED          ' WS-EDIT-COUNT.
