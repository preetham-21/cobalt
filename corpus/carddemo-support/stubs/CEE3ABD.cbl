       IDENTIFICATION DIVISION.
       PROGRAM-ID. CEE3ABD.
      *****************************************************************
      * CEE3ABD - STAND-IN FOR THE LANGUAGE ENVIRONMENT ABEND SERVICE.
      * CARDDEMO CALLS IT ON UNRECOVERABLE I/O ERRORS. OFF THE
      * MAINFRAME WE REPORT THE ABEND CODE AND END THE RUN WITH
      * RETURN-CODE 12 SO THE OUTCOME IS DETERMINISTIC AND COMPARABLE.
      *****************************************************************
       DATA DIVISION.
       LINKAGE SECTION.
       01  LK-ABCODE                   PIC S9(9) BINARY.
       01  LK-TIMING                   PIC S9(9) BINARY.
       PROCEDURE DIVISION USING LK-ABCODE LK-TIMING.
           DISPLAY 'CEE3ABD: ABEND CODE ' LK-ABCODE
           MOVE 12 TO RETURN-CODE
           STOP RUN.
