"""Formatting for Indian rupees. All arithmetic stays in integer paise."""


def rupees(paise: int | None) -> str:
    """12999900 -> '₹1,29,999' (lakh grouping); fractional paise shown only when present."""
    if paise is None:
        return ""
    negative = paise < 0
    whole, frac = divmod(abs(int(paise)), 100)
    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups) + "," + tail
    text = f"₹{digits}" + (f".{frac:02d}" if frac else "")
    return f"-{text}" if negative else text
