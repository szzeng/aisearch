"""Unit suites of the photo-search catalog.

The marker file exists so pytest imports these modules as ``unit.*`` instead of bare
top-level names: P3-08B ships a ``test_p3_face_redetection_decisions`` in this directory
*and* one in ``tests/integration`` (the frozen matrix names both), and two modules with the
same basename cannot be collected in one session otherwise.
"""
