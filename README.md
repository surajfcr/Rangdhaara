# Rangdhara Art Studio & Academy

The online store for ready-to-buy textured art, clay decor and DIY kits, plus the
Rangdhara Art Academy (video masterclasses and live workshops), with an admin panel
for orders, stock, courses and revenue.

- **Backend:** Python 3.11+, FastAPI, SQLite
- **Frontend:** HTML, Tailwind (CDN), vanilla JavaScript modules
- **Payments:** Razorpay, Cashfree, manual UPI, or a mock gateway for development

---

## Run it

**Windows:** double-click `run.bat`.
**macOS / Linux:** `./run.sh`

The first run:

1. creates a Python environment (on Windows it lives in `%LOCALAPPDATA%\Rangdhaara\venv`, outside OneDrive, so sync can't lock it),
2. installs dependencies,
3. copies `.env.example` to `.env`,
4. creates the database at `data/rangdhaara.db` and loads the catalogue,
5. imports customers and orders from the old site (`data/legacy/*.json`), once,
6. asks you to create the owner account,
7. opens <http://localhost:8080>. The admin panel is at <http://localhost:8080/admin>.

Later runs skip what's already done. Stop the server with **Ctrl+C**.

### In development (the default)

- **Payments** use a test gateway page where you choose whether the payment succeeds, fails, or succeeds with a lost webhook. No money moves.
- **Emails** aren't sent. They're saved as HTML files in `data/outbox/`, and one-time sign-in codes are printed in the server window.

### Useful commands

```bash
python manage.py create-admin
```
```bash
python manage.py set-role someone@example.com staff
```
```bash
python manage.py check
```
```bash
python manage.py backup
```
```bash
python manage.py send-test-email you@example.com
```
```bash
python -m unittest discover -s tests -v
```

(On Windows, use the environment's Python: `%LOCALAPPDATA%\Rangdhaara\venv\Scripts\python.exe`.)

---

## After the first run

- **Imported customers must set a new password.** The old site stored passwords in plain text, so they were hashed on import and each account is asked to choose a new one at its next sign-in (a code is emailed).
- **The one old order is flagged "Unverified old-site payment".** The old site confirmed orders without checking payment. Match it against your UPI records in **Admin → Orders**, then mark it paid or cancel it.
- **Stock counts are placeholders (5 each).** Set real numbers in **Admin → Catalogue**.
- **Van Gogh 'Starry Night' is priced at ₹3,200.** The old file had a ₹1 test price; ₹3,200 was the only other figure recorded for it.
- **Six drafts and four Academy items start switched off:** the tulips, Shiva, sun-calendar, trinket-dish, coaster and magnet pieces photographed in `assets/images`, three masterclasses, and a weekend workshop. Each needs a price (and, for courses, lessons) before **Publish** will work.

---

## Using the admin panel

Open `/admin` and sign in with the owner account.

- **Add a product:** Products & stock → **Add product**. Enter the name, price, pieces in stock and a photo, and it can go live straight away.
- **Change stock:** type the new number in the *Pieces in stock* column and press Enter. At 0 the store shows *Sold out*.
- **Remove a product:** click the bin icon. Items that were never ordered are deleted. Items with past orders are archived instead (hidden from the store, order history kept) and can be restored from the *Archived* tab.
- **Create a course:** Courses → **Create a course**, then add modules and lessons, upload a video for each lesson, and press **Publish**.
- **Client details:** Customers lists every account. Click a name to see their contact details, saved addresses, orders and courses. **Download all as CSV** exports the list; each export is recorded in the audit log.

---

## Going live at rangdhara.in

This deploys to **Render** (`render.yaml` in this repo defines the whole service). Email starts on a free Gmail address and can move to a proper `@rangdhara.in` mailbox later — see step 3. `python manage.py check` lists what's still missing at any point; the server refuses to start with `APP_ENV=production` until everything required is set.

### 1. Put the code on GitHub

Render deploys from a Git repository, so the code needs to live on GitHub first.

1. Create a new **empty** repository on GitHub (public or private — either works with Render).
2. In this folder, run:
   ```bash
   git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPO.git
   git push -u origin master
   ```
   Windows will pop up a browser sign-in the first time — that's normal.

### 2. Deploy on Render

1. Sign up at [render.com](https://render.com) and connect your GitHub account.
2. **New → Blueprint**, pick this repository. Render reads `render.yaml` and sets up the web service and its persistent disk automatically.
3. Before the first deploy, open the service's **Environment** tab and fill in the values marked secret in `render.yaml` (`UPI_VPA`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM`, `STORE_ADMIN_EMAIL`) — see the email step below for where the SMTP values come from. Deploy.
4. Once it's live at the `.onrender.com` address Render gives you, add your custom domain: service **Settings → Custom Domains → Add** → enter `rangdhara.in` (and `www.rangdhara.in` if you want that to work too). Render then shows you exactly which DNS record(s) to add — usually one **A** record for `rangdhara.in` and one **CNAME** for `www`.
5. In [GoDaddy's DNS manager](https://dcc.godaddy.com/domains) for `rangdhara.in`, add exactly the records Render showed you. DNS changes can take up to a few hours to take effect; Render issues the HTTPS certificate automatically once it sees the domain pointed at it.

### 3. Set up email — free Gmail now, `@rangdhara.in` later

**Now (free):** create a normal Gmail address (e.g. `support.rangdhara@gmail.com`) at [accounts.google.com/signup](https://accounts.google.com/signup). Sign in to it → Google Account → **Security** → turn on **2-Step Verification** → search for **"App passwords"** → create one. Then in Render:
- `SMTP_USER` = that Gmail address
- `SMTP_PASSWORD` = the 16-character app password (not the account's normal login password)
- `MAIL_FROM` = `Rangdhara Art Studio <that address>`
- `STORE_ADMIN_EMAIL` = the same address (where new-order and needs-review alerts go)

One inbox is enough at this stage — it both sends automated mail and receives replies/questions.

**Later (paid, when it's worth it):** buy Google Workspace or Zoho Mail for `rangdhara.in`, create `contact@rangdhara.in` and `support@rangdhara.in`, verify the domain (a TXT record in GoDaddy) and add the MX + SPF records it gives you — then just swap the 4 values above for the new addresses in Render. No code or redeploy needed.

### 4. Create the real admin account

Once deployed, open a **Shell** from the Render service dashboard and run:
```bash
python manage.py create-admin
```
This is the account you (or Rangdhara, using her own email) sign in with at `https://rangdhara.in/admin`. Run `python manage.py create-admin` again with a different email to give her a separate login — see [Using the admin panel](#using-the-admin-panel).

### 5. Payments

The Blueprint starts you on **manual UPI** (`PAYMENT_PROVIDER=manual_upi`) — customers see your UPI QR code and pay you directly, and you confirm each order as paid in the admin panel. No gateway account needed to launch.

When you're ready for automatic card/UPI/netbanking checkout:
- **Razorpay:** complete their KYC, then set `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, and `PAYMENT_PROVIDER=razorpay` in Render. In the Razorpay dashboard, add the webhook `https://rangdhara.in/api/v1/webhooks/razorpay` for `payment.captured`, `payment.failed` and `order.paid`.
- **Cashfree:** complete their KYC, then set `CASHFREE_APP_ID`, `CASHFREE_SECRET_KEY`, `CASHFREE_ENV=production`, and `PAYMENT_PROVIDER=cashfree`. Add the webhook `https://rangdhara.in/api/v1/webhooks/cashfree`.

### 6. Backups

A snapshot of the database is written to the persistent disk (`/var/data/data/backups`) every day and the last 14 are kept. Since that disk lives only on Render, periodically download a copy somewhere else too (Render's dashboard shell can `cat` a backup file, or add an off-site backup step later) — a disk failure without a second copy would lose everything.

**System health** in the admin panel shows the same checklist live, plus email delivery, background jobs and backup status.

---

## How the important parts work

- **Prices come from the server.** The browser sends only product option ids and quantities. Totals, discounts and shipping are calculated in `app/pricing.py`.
- **Payment is confirmed only by the gateway.** A signed webhook is verified, then the payment is fetched from the gateway's API and its amount checked before the order is marked paid. Duplicate webhooks are ignored. Orders whose webhook never arrived are re-checked every 10 minutes, and customers can press "Check again".
- **Stock is held during checkout** (15 minutes, or 12 hours for manual UPI), so one-of-a-kind pieces can't be sold twice.
- **Accounts:** passwords are hashed with argon2id, sessions live in an HttpOnly cookie, and one-time codes expire after 10 minutes and allow 5 tries. Code requests never reveal whether an account exists.
- **Course videos** are private files, streamed through links that expire after 4 hours and stop working if access is refunded.
- **Roles:** *staff* can pack and ship orders and update stock. The *owner* (admin) can also change prices, refunds, courses, student access and roles. Every admin change is written to the audit log.

## Project layout

```text
app/            FastAPI backend
  routers/      JSON API: auth, store, account, academy, admin
  payments/     mock, razorpay, cashfree, manual_upi + webhook handling
public/         everything the web server may serve
  assets/js/    store/, order/, learn/, admin/, lib/
data/           database, backups, email outbox, legacy import (not in git)
storage/        lesson videos, downloads, certificates (not in git)
tests/          end-to-end backend tests
manage.py       setup and admin commands
run.bat / run.sh
```
