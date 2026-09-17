"""Local suggestions, not proof of carrier ownership. See docs/courier-rules.md."""

RULES = [
    {"code": "shunfeng", "pattern": r"^SF[0-9]{13}$", "auto": True},
    {"code": "yuantong", "pattern": r"^YT[0-9]{13}$", "auto": True},
    {"code": "zhongtong", "pattern": r"^79[01][0-9]{11}$", "auto": True},
    {"code": "shentong", "pattern": r"^77[0-9]{13}$", "auto": True},
    {"code": "jd", "pattern": r"^(?:JD|JDL)[0-9]{12,13}$", "auto": True},
    {"code": "jtexpress", "pattern": r"^JT[0-9]{13}$", "auto": True},
    {"code": "debangkuaidi", "pattern": r"^DPK[0-9]{12}$", "auto": True},
    {"code": "yunda", "pattern": r"^YD[0-9]{12}$", "auto": True},
    {"code": "ems", "pattern": r"^E[A-Z][0-9]{9}CN$", "auto": True},
    # Legacy numeric formats overlap: suggestions only, never pick the first match.
    {"code": "shunfeng", "pattern": r"^[0-9]{12}$", "auto": False},
    {"code": "zhongtong", "pattern": r"^[0-9]{12}$", "auto": False},
    {"code": "shentong", "pattern": r"^[0-9]{12}$", "auto": False},
]
