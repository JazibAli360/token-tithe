"""Parse and format page ranges such as "1-3,5,7-8"."""


def parse_ranges(text):
    """Expand "1-3,5" into [1, 2, 3, 5]. Whitespace is ignored; output is sorted and unique."""
    pages = set()
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            start, end = (int(x) for x in part.split("-", 1))
            if start > end:
                raise ValueError(f"descending range: {part}")
            pages.update(range(start, end + 1))
        else:
            pages.add(int(part))
    return sorted(pages)


def format_ranges(pages):
    """Collapse [1, 2, 3, 5] into "1-3,5". Input may be unsorted and contain duplicates."""
    nums = sorted(set(pages))
    out = []
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.extend(str(n) for n in nums[i : j + 1])
        i = j + 1
    return ",".join(out)
