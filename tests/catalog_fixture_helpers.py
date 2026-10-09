import yaml

from app.bodygraph import CHANNELS, GATE_CENTERS


CIRCUIT_CHANNELS = {
    "integration": ((10, 20), (10, 57), (20, 34), (34, 57)),
    "centering": ((10, 34), (25, 51)),
    "knowing": ((1, 8), (2, 14), (3, 60), (12, 22), (20, 57),
                (23, 43), (24, 61), (28, 38), (39, 55)),
    "ego": ((19, 49), (21, 45), (26, 44), (32, 54), (37, 40)),
    "defense": ((6, 59), (27, 50)),
    "logic": ((4, 63), (5, 15), (7, 31), (9, 52), (16, 48), (17, 62), (18, 58)),
    "sensing": ((11, 56), (13, 33), (29, 46), (30, 41), (35, 36), (42, 53), (47, 64)),
}


def fixture_catalog():
    pairs = sorted(tuple(sorted(pair)) for pair in CHANNELS)
    circuit_by_pair = {
        tuple(sorted(pair)): circuit_id
        for circuit_id, members in CIRCUIT_CHANNELS.items()
        for pair in members
    }
    return {
        "schema_version": 2,
        "gates": [
            {
                "number": gate,
                "name": f"Sample gate {gate}",
                "description": f'Sample description {gate}, with "quotes"\nand another line.',
                "center": GATE_CENTERS[gate],
            }
            for gate in range(1, 65)
        ],
        "circuits": [
            {"id": circuit_id, "name": f"Sample circuit {circuit_id}",
             "description": f"Sample circuit description {circuit_id}"}
            for circuit_id in CIRCUIT_CHANNELS
        ],
        "channels": [
            {"gates": [a, b], "circuit_id": circuit_by_pair[(a, b)],
             "name": f"Sample channel {a}-{b}",
             "description": "Sample channel description"}
            for a, b in pairs
        ],
    }


def write_fixture(path, catalog=None):
    with path.open("w", encoding="utf-8") as target:
        yaml.safe_dump(fixture_catalog() if catalog is None else catalog, target,
                       allow_unicode=True, sort_keys=False)
