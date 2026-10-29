# PERSONAL BUDGET ALLOWANCE

## BSIT Class Project Concept Paper

**Reynaldo Ilustrisimo | Rico Gasataya | Jun Gengos**
*10/7/2026 — 100 Points*

--- Background of the Project

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Managing a personal allowance is a critical life skill, particularly for students and young
adults who receive a fixed amount of money on a daily, weekly, or monthly basis. Without a
structured approach to tracking income, expenses, and savings, individuals often find themselves
overspending in some categories while underfunding essential needs. The absence of a clear
budget plan frequently leads to financial stress, unmet obligations, and missed savings
opportunities before the next allowance period begins.

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;In practice, many students rely on informal methods of financial tracking, such as handwritten
notes, basic calculator apps, or mental estimates. These approaches lack the structure, consistency,
and analytical feedback needed to support informed spending decisions. They also make it
difficult to compare planned spending against actual expenses, identify problem categories, or
monitor progress toward a specific savings goal over time.

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;To address these limitations, the proposed Personal Budget Allowance project will develop a
web-based application that enables users to record their allowance, plan category-level budget
allocations, track individual expense items, monitor spending against their plan, and manage
savings goals. The system will provide a Smart Budget Planner that generates suggested
allocations based on the user's allowance amount and budget period, as well as a dashboard that
presents a real-time summary of financial activity. By consolidating these functions into a single,
accessible web application, the project will give users the tools they need to take control of their
allowance and develop practical, lasting financial habits.

---

### Statement of the Problem or Project Objectives

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;The main objective of this project is to design, develop, and evaluate a web-based Personal
Budget Allowance system that enables users to plan, allocate, track, and analyze their personal
allowance through an organized and easy-to-use digital interface.

Specifically, the project aims to accomplish the following technical objectives:

1. Develop an allowance entry module that allows users to record received allowances by
   amount, type (daily, weekly, bi-weekly, or monthly), label, date, and optional description,
   with a complete history of added and deleted records.

2. Implement a Smart Budget Planner that generates suggested category-level allocations
   (Food, Transportation, School Expenses, Personal Needs, Savings, Emergency Fund, and
   Other) based on the user's entered allowance amount and selected budget period, and
   allows the user to review, adjust, name, and save the plan before it takes effect.

3. Develop an expense recording module that allows users to add expense items with a name,
   category, quantity, unit price, date, and status (Planned, Purchased, or Cancelled), so that
   only confirmed purchases affect the actual available balance while planned items remain
   visible for review.

4. Integrate a budget monitoring feature that compares each category's planned allocation
   against actual recorded spending, displays a progress bar and remaining amount for each
   category, and generates warnings when a category approaches or exceeds its planned limit.

5. Develop a savings goals module that allows users to create named savings targets with
   optional deadlines, record actual contributions that reduce the available balance, and
   monitor progress toward each goal separately from planned budget allocations.

6. Provide a dashboard that displays the total allowance received, available balance, total
   expenses, total savings contributed, and a spending summary organized by category and
   budget period (daily, weekly, or monthly), supported by a recent activity log and an
   activity history page.

7. Conduct system testing to verify that all financial calculations are accurate, that saving a
   budget plan does not alter the actual balance, that expense status transitions are handled
   correctly without duplicate deductions, and that each user can access only their own data.

---

### Significance of the Project

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;The proposed system will provide practical value to the following beneficiaries:

- **Students and Young Adults:** Provides a structured, accessible tool for managing a
  fixed allowance across multiple expense categories, helping users avoid overspending,
  identify problem areas in their budget, and build consistent savings habits over time.

- **Parents and Guardians:** Offers indirect benefit by helping dependents develop
  responsible money management skills, potentially reducing requests for additional funds
  and supporting the family's overall financial planning.

- **Future Researchers and Students:** Serves as a practical reference and working
  demonstration of a Django-based personal finance web application, covering concepts
  such as financial data modeling, rule-based budget recommendation, and user-scoped
  transaction management.

---

### Alignment with Institutional Research Agenda and Sustainable Development Goals

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;**Institutional Research Agenda Alignment:** *(Note: The specific title of the
institutional research agenda requires verification with the program faculty or the Office of
Research before submission.)* The project aligns with Information Technology application
development and digital innovation priorities by applying web technologies to a practical
everyday problem. It demonstrates the use of software engineering principles, database-backed
data management, and rule-based analytical systems to deliver a functional and user-centered
solution.

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;**Sustainable Development Goal Alignment:**

- **SDG 4 (Quality Education):** Supports financial literacy and responsible
  decision-making as practical life skills developed through the use of a structured
  budgeting tool, complementing formal education with applied technology.

- **SDG 9 (Industry, Innovation, and Infrastructure):** Demonstrates the application of
  web-based software innovation to a real-world personal finance problem, contributing to
  the development of accessible digital tools for everyday use.

- **SDG 10 (Reduced Inequalities):** Provides a freely accessible, low-barrier budgeting
  tool that does not require paid financial services or bank accounts, making structured
  financial planning available to students and individuals regardless of economic background.

---

## SCOPE AND LIMITATIONS

### System Scope and Technical Architecture

| Component | Description and Coverage |
|---|---|
| **Core Modules** | Allowance Entry and History; Smart Budget Planner with goal-aware suggested allocations; Expense Recording with Planned, Purchased, and Cancelled status; Budget Plan Monitoring per category; Savings Goals with individual contribution tracking; Dashboard with balance summary, spending by category, and activity log; Transaction and Activity History. |
| **Technology Stack** | Backend: Python 3 with the Django web framework. Database: SQLite (development). Frontend: HTML5, CSS3, JavaScript (ES6), Bootstrap via Tabler UI, Font Awesome icons, ApexCharts for visual summaries. Rule-based budget recommendation engine implemented in pure Python with no external AI APIs. |
| **Target Users** | Individual students and young adults who receive a personal allowance and wish to plan, track, and review their spending and savings through a web browser. |
| **Data Scope** | All financial records — allowances, expenses, budget plans, savings goals, contributions, and activity logs — are stored per authenticated user. Users cannot view or modify another user's data. |
| **Budget Categories** | Food, Transportation, School Expenses, Personal Needs, Savings, Emergency Fund, and Other. These seven categories are used consistently across the Smart Budget Planner, expense recording, budget tracking, dashboard summaries, and the recommendation engine. |
| **Budget Periods** | Daily, Weekly, and Monthly. The Smart Budget Planner and dashboard support all three periods. Bi-weekly is available for allowance entry labels but is not a separate budget planning period. |
| **Savings Distinction** | The system maintains a clear separation between planned savings (the amount allocated to savings in a budget plan) and actual savings (money contributed to a savings goal through the Goals module). Saving a budget plan does not deduct money from the available balance. |

---

### Limitations

- The system relies entirely on information entered manually by the user. It does not
  connect to any bank account, e-wallet, or external financial service. Balance figures
  reflect only what the user has recorded within the application.

- The budget recommendation engine uses rule-based percentage splits and goal data
  entered by the user. It does not learn from historical spending patterns over time and
  does not use a machine learning model or a generative AI service.

- The application is designed for single-user personal use. It does not include
  multi-household budgeting, shared budget plans, or family account management.

- Expense categories are fixed at seven predefined options. Custom user-defined
  categories are not supported in the current implementation.

- The system is developed and tested as a local web application. Deployment to a
  production server, configuration of HTTPS, and optimization for high-traffic use are
  outside the scope of this project phase.

- Recurring expense automation (for example, automatically generating a monthly bill
  entry each period) is not implemented. Users must record each expense manually.

- Data export, printable reports, and PDF generation of budget summaries are not
  included in the current scope.
