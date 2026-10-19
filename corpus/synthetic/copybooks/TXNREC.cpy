      *****************************************************************
      * TXNREC - DAILY TRANSACTION RECORD (40 BYTES, LINE SEQUENTIAL)
      * SORTED ASCENDING BY TXN-ACCT-ID, TXN-SEQ.
      *****************************************************************
       01  TRANSACTION-RECORD.
           05  TXN-ACCT-ID             PIC 9(8).
           05  TXN-CODE                PIC X(2).
               88  TXN-DEPOSIT         VALUE 'DP'.
               88  TXN-WITHDRAWAL      VALUE 'WD'.
               88  TXN-FEE             VALUE 'FE'.
           05  TXN-AMOUNT              PIC 9(7)V99.
           05  TXN-SEQ                 PIC 9(6).
           05  FILLER                  PIC X(15).
