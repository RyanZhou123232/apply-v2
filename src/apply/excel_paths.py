"""Live Excel workbooks live on Ryan's PC only.

Writers must edit these paths in place on the PC (machine-targeted shell).
Never copy a workbook to the box, edit it, and copy it back. Never write a
sidecar backup (.bak, .backup-*.xlsx) or a tracking\\backups folder; verify
by reading the live file back after the write.
"""
from pathlib import Path

PC_PROJECT = Path(r"E:\自动投递工作流v2")
PC_TRACKING = PC_PROJECT / "tracking"
LIVE_TRACKER = PC_TRACKING / "tech投递2027.xlsx"
LIVE_ACCOUNTS = PC_TRACKING / "公司账号.xlsx"
