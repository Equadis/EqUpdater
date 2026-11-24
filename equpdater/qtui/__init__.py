"""EqUpdater 3.0 front end: PySide6 + Qt Quick (QML).

The only layer that imports Qt. Everything it shows or does comes from
``equpdater.core`` through the QObjects in ``bridge.py``; QML never touches
files, the network or business rules.
"""
