"""District conference Excel export includes roster fields and the payment summary."""

from openpyxl import load_workbook

from app.common.exporter import create_official_conference_excel


def test_official_conference_excel_includes_preferences_and_payment():
    blob = create_official_conference_excel(
        officials=[
            {
                "name": "Justin Joseph",
                "phone": "9999999999",
                "gender": "M",
                "unit_name": "St Thomas",
                "food_preference": "veg",
                "accommodation_required": True,
            }
        ],
        members=[
            {
                "name": "Anita",
                "number": "8888888888",
                "gender": "F",
                "unit_name": "St Mary",
                "food_preference": "non-veg",
                "accommodation_required": False,
            }
        ],
        ledger={
            "overall_status": "partial",
            "fee_owed": 1500,
            "total_paid": 500,
            "balance_due": 1000,
        },
    )

    sheet = load_workbook(blob).active
    assert [cell.value for cell in sheet[1]] == [
        "Type",
        "Name",
        "Phone",
        "Gender",
        "Unit",
        "Food Preference",
        "Accommodation",
        "Registration Status",
        "Payment Status",
        "Amount Due",
        "Amount Paid",
        "Balance Due",
    ]
    assert [cell.value for cell in sheet[2]] == [
        "Official",
        "Justin Joseph",
        "9999999999",
        "Male",
        "St Thomas",
        "Veg",
        "Yes",
        "Registered",
        None,
        None,
        None,
        None,
    ]
    assert sheet["A3"].value == "Delegate"
    assert sheet["F3"].value == "Non-Veg"
    assert sheet["G3"].value == "No"
    assert [sheet["A4"].value, sheet["I4"].value, sheet["J4"].value, sheet["K4"].value, sheet["L4"].value] == [
        "Payment",
        "Partially paid",
        1500,
        500,
        1000,
    ]
