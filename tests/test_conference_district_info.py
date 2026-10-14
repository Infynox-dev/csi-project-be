"""Conference district aggregation from attendee lists."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from openpyxl import load_workbook

from app.common.exporter import create_conference_excel
from app.conference.service import district_info_from_attendee_lists


def test_district_info_from_attendee_lists_counts_and_food():
    info = district_info_from_attendee_lists(
        officials=[
            {
                "id": 1,
                "member_id": None,
                "name": "District Login",
                "phone": "9000000001",
                "gender": "M",
                "unit_name": "St Thomas",
                "food_preference": "veg",
            },
            {
                "id": 2,
                "member_id": 100,
                "name": "Official Member",
                "phone": "9000000002",
                "gender": "F",
                "unit_name": "St Mary",
                "food_preference": "non-veg",
            },
        ],
        members=[
            {
                "id": 3,
                "member_id": 200,
                "name": "Delegate",
                "phone": "9000000003",
                "gender": "M",
                "unit_name": "St Paul",
                "food_preference": "veg",
            },
        ],
    )

    assert info["count_of_officials"] == 2
    assert info["count_of_members"] == 1
    assert info["count_of_male_officials"] == 1
    assert info["count_of_female_officials"] == 1
    assert info["count_of_male_members"] == 1
    assert info["total_count"] == 3
    assert info["veg_count"] == 2
    assert info["non_veg_count"] == 1
    assert info["officials"][0]["unit"] == "St Thomas"
    assert info["members"][0]["phone"] == "9000000003"


def test_create_conference_excel_from_district_info():
    district_info = district_info_from_attendee_lists(
        officials=[
            {
                "id": 1,
                "member_id": None,
                "name": "A",
                "phone": "1",
                "gender": "M",
                "unit_name": "Unit A",
                "food_preference": "veg",
            }
        ],
        members=[],
    )
    blob = create_conference_excel({"THIRUVALLA": district_info}, conference_id=8)
    sheet = load_workbook(blob).active
    assert sheet["A2"].value == "THIRUVALLA"
    assert sheet["D2"].value == "A"
    assert sheet["B3"].value == "Counts"
