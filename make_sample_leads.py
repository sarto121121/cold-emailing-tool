"""Creates a sample leads.xlsx so you can see the expected format. Replace it with your real leads."""
from openpyxl import Workbook

wb = Workbook()
ws = wb.active
ws.append(["name", "email", "company", "website"])
ws.append(["Ali Khan", "ali@example.com", "Example Dental Clinic", "example.com"])
ws.append(["", "info@example.org", "Example Org", "https://www.example.org"])
ws.append(["Sara", "not-an-email", "Broken Row", "example.net"])
for col, width in zip("ABCD", (18, 28, 28, 30)):
    ws.column_dimensions[col].width = width
wb.save("leads.xlsx")
print("leads.xlsx created")
