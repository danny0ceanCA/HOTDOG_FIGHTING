import pandas as pd

# Change pandas display options
pd.set_option("display.max_rows", None)
pd.set_option("display.max_columns", None)

# Load rate sheet
file_path = r"C:\Users\danie\Documents\ASCS\Test Project\TEST 2\Test Rates.xlsx"
rate_sheet = pd.read_excel(file_path)

print("Rate Sheet:")
print(rate_sheet.head())

# Load payroll data
file_path_for_payroll_data = r"C:\Users\danie\Documents\ASCS\Test Project\May 2024.xlsx"
payroll_data = pd.read_excel(file_path_for_payroll_data)

print("Payroll Data:")
print(payroll_data.sample(10))

# Filter and select relevant columns
Employee_names = payroll_data[
    (payroll_data["Employee"] != "Unassigned")
][["Employee", "License", "Verified Date", "Hours"]]

print("Employee Names:")
print(Employee_names.head())

# Merge with rate sheet
Employees_with_rates = pd.merge(Employee_names, rate_sheet, on="License", how="outer")

# Ensure numeric types for calculations
Employees_with_rates["Rate"] = pd.to_numeric(Employees_with_rates["Rate"], errors="coerce")
Employees_with_rates["Hours"] = pd.to_numeric(Employees_with_rates["Hours"], errors="coerce")

# Fill missing values in Rate or Hours with 0
Employees_with_rates["Rate"] = Employees_with_rates["Rate"].fillna(0)
Employees_with_rates["Hours"] = Employees_with_rates["Hours"].fillna(0)

# Recalculate the Total column
Employees_with_rates["Calculated Total"] = (
    Employees_with_rates["Rate"] * Employees_with_rates["Hours"]
).round(2)

# Check for discrepancies between Calculated Total and existing Total (if present)
if "Total" in Employees_with_rates.columns:
    Employees_with_rates["Discrepancy"] = (
        Employees_with_rates["Calculated Total"] != Employees_with_rates["Total"]
    )
    discrepancies = Employees_with_rates[Employees_with_rates["Discrepancy"]]
    if not discrepancies.empty:
        print("Discrepancies Found:")
        print(discrepancies)

# Use recalculated Total going forward
Employees_with_rates["Total"] = Employees_with_rates["Calculated Total"]

# Group by "Verified Date" and aggregate
grouped_employees = Employees_with_rates.groupby("Verified Date", as_index=False).agg({
    "Employee": "first",
    "License": "first",
    "Rate": "first",
    "Hours": "sum",
    "Total": "sum",
})

# Add a Grand Total Row
grand_total = grouped_employees[["Hours", "Total"]].sum()
grand_total_row = pd.DataFrame([{
    "Verified Date": "Grand Total",
    "Employee": "",
    "License": "",
    "Rate": "",
    "Hours": grand_total["Hours"],
    "Total": grand_total["Total"],
}])
grouped_employees = pd.concat([grouped_employees, grand_total_row], ignore_index=True)

# Write the result to an Excel file
output_path = r"C:\Users\danie\Downloads\Employee List with Rate by License.xlsx"
grouped_employees.to_excel(output_path, index=False)

print("Grouped Employees with Rates:")
print(grouped_employees)
