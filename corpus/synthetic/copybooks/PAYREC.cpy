      *****************************************************************
      * PAYREC - PAYROLL INPUT RECORD (60 BYTES, LINE SEQUENTIAL)
      * ONE HEADER, N DETAIL RECORDS, ONE TRAILER. THE BODY IS
      * REDEFINED PER RECORD TYPE.
      *****************************************************************
       01  PAY-RECORD.
           05  PAY-REC-TYPE            PIC X.
               88  PAY-HEADER          VALUE 'H'.
               88  PAY-DETAIL          VALUE 'D'.
               88  PAY-TRAILER         VALUE 'T'.
           05  PAY-BODY                PIC X(59).
           05  PAY-HEADER-BODY REDEFINES PAY-BODY.
               10  PAY-PERIOD-END      PIC 9(8).
               10  PAY-COMPANY         PIC X(30).
               10  FILLER              PIC X(21).
           05  PAY-DETAIL-BODY REDEFINES PAY-BODY.
               10  EMP-ID              PIC 9(6).
               10  EMP-NAME            PIC X(20).
               10  EMP-PAY-TYPE        PIC X.
                   88  EMP-HOURLY      VALUE 'H'.
                   88  EMP-SALARIED    VALUE 'S'.
      *        HOURLY RATE, OR ANNUAL SALARY FOR SALARIED STAFF
               10  EMP-RATE            PIC 9(7)V9(4).
               10  EMP-HOURS           PIC 9(3)V99.
               10  EMP-TAX-CODE        PIC X.
               10  EMP-PRETAX-DED      PIC 9(5)V99.
               10  FILLER              PIC X(8).
           05  PAY-TRAILER-BODY REDEFINES PAY-BODY.
               10  PAY-EXPECTED-COUNT  PIC 9(6).
               10  PAY-EXPECTED-HASH   PIC 9(12).
               10  FILLER              PIC X(41).
