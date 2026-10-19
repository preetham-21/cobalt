      *****************************************************************
      * CUSTREC - CUSTOMER MASTER RECORD (80 BYTES, LINE SEQUENTIAL)
      *****************************************************************
       01  CUSTOMER-RECORD.
           05  CUST-ID                 PIC 9(6).
           05  CUST-NAME               PIC X(25).
           05  CUST-REGION             PIC X(2).
           05  CUST-BALANCE            PIC S9(7)V99
                                       SIGN LEADING SEPARATE.
           05  CUST-CREDIT-LIMIT       PIC 9(7)V99.
           05  CUST-STATUS             PIC X.
               88  CUST-ACTIVE         VALUE 'A'.
               88  CUST-CLOSED         VALUE 'C'.
           05  FILLER                  PIC X(27).
