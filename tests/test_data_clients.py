from pump_detector import data_clients


def test_collect_paginated_rows_walks_backwards_and_returns_oldest_first():
    calls: list[dict[str, object]] = []

    def fetch_page(params: dict[str, object]):
        calls.append(dict(params))
        if "endTime" not in params:
            return [{"timestamp": "3000"}, {"timestamp": "2000"}]
        if params["endTime"] == 1999:
            return [{"timestamp": "1000"}]
        return []

    rows = data_clients._collect_paginated_rows(
        fetch_page,
        limit=3,
        page_size=2,
        timestamp_key="timestamp",
        end_param="endTime",
    )

    assert [row["timestamp"] for row in rows] == ["1000", "2000", "3000"]
    assert calls == [{"limit": 2}, {"limit": 1, "endTime": 1999}]


def test_collect_paginated_rows_trims_to_requested_latest_window():
    def fetch_page(params: dict[str, object]):
        if "end" not in params:
            return [[5000, "newest"], [4000, "newer"]]
        if params["end"] == 3999:
            return [[3000, "old"], [2000, "older"]]
        return []

    rows = data_clients._collect_paginated_rows(
        fetch_page,
        limit=3,
        page_size=2,
        timestamp_key=0,
        end_param="end",
    )

    assert rows == [[3000, "old"], [4000, "newer"], [5000, "newest"]]
