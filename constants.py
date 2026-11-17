POSITIONS = ["PG", "SG", "SF", "PF", "C"]
SEASONS = ["2023-24", "2024-25", "2025-26"]

SHOT_ZONES = [
    "Restricted Area",
    "In The Paint (Non-RA)",
    "Mid-Range",
    "Left Corner 3PT",
    "Right Corner 3PT",
    "Above The Break 3PT",
]

SHOT_ZONE_GEOMETRY = {
    "Restricted Area": {
        "path": "M 210,430 L 210,390 A 40,40 0 0,1 290,390 L 290,430 Z",
        "text_x": 250,
        "text_y": 410,
        "text_dy": 12,
    },
    "In The Paint (Non-RA)": {
        "path": "M 170,430 L 170,240 L 330,240 L 330,430 L 290,430 L 290,390 A 40,40 0 0,0 210,390 L 210,430 Z",
        "text_x": 250,
        "text_y": 315,
        "text_dy": 12,
    },
    "Mid-Range": {
        "path": "M 30,430 L 30,290 A 237.5,237.5 0 0,1 470,290 L 470,430 L 330,430 L 330,240 L 170,240 L 170,430 Z",
        "text_x": 250,
        "text_y": 200,
        "text_dy": 13,
    },
    "Left Corner 3PT": {
        "path": "M 0,430 L 30,430 L 30,290 L 0,290 Z",
        "text_x": 15,
        "text_y": 360,
        "text_dy": 11,
    },
    "Right Corner 3PT": {
        "path": "M 470,430 L 500,430 L 500,290 L 470,290 Z",
        "text_x": 485,
        "text_y": 360,
        "text_dy": 11,
    },
    "Above The Break 3PT": {
        "path": "M 0,0 L 500,0 L 500,430 L 470,430 L 470,290 A 237.5,237.5 0 0,0 30,290 L 30,430 L 0,430 Z",
        "text_x": 250,
        "text_y": 100,
        "text_dy": 13,
    },
}
