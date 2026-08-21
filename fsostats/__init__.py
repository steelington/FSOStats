"""FSOSTATS - Aces High Friday Squad Operations statistics."""

# Single source of truth for the app version, shown in the window title
# and masthead so a screenshot or bug report identifies the build, and
# compared against the newest GitHub release by fsostats.version_check.
#
# Bump this in the same commit that tags a release - a git tag on its own
# leaves the running app claiming the previous version.
__version__ = "0.2.0"
