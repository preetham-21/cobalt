      *****************************************************************
      * ACCTREC - ACCOUNT MASTER RECORD (40 BYTES, LINE SEQUENTIAL)
      * SORTED ASCENDING BY ACCT-ID.
      *****************************************************************
       01  ACCOUNT-RECORD.
           05  ACCT-ID                 PIC 9(8).
           05  ACCT-TYPE               PIC X.
               88  ACCT-SAVINGS        VALUE 'S'.
               88  ACCT-CHECKING       VALUE 'C'.
               88  ACCT-LOAN           VALUE 'L'.
           05  ACCT-BALANCE            PIC S9(9)V99
                                       SIGN LEADING SEPARATE.
           05  ACCT-OPEN-DATE          PIC 9(8).
           05  ACCT-OPEN-DATE-R REDEFINES ACCT-OPEN-DATE.
               10  ACCT-OPEN-YYYY      PIC 9(4).
               10  ACCT-OPEN-MM        PIC 9(2).
               10  ACCT-OPEN-DD        PIC 9(2).
           05  ACCT-STATUS             PIC X.
               88  ACCT-ACTIVE         VALUE 'A'.
               88  ACCT-CLOSED         VALUE 'C'.
           05  FILLER                  PIC X(10).
