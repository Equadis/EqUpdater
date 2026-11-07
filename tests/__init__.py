import os

# The "manage what is already here" question is a modal dialog: a test that
# wants it asks for it (see tests/test_migration.py).
os.environ["EQUPDATER_NO_MIGRATION_PROMPT"] = "1"
# Likewise the one-time Defender exclusion question (tests/test_defender_prompt.py).
os.environ["EQUPDATER_NO_DEFENDER_PROMPT"] = "1"
