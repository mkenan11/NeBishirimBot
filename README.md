# 🍽️ NeBishirimBot

**NeBishirimBot** is an AI-powered Telegram bot that helps users decide what to cook based on the ingredients they already have at home.

Users can add ingredients manually or send a photo, then receive recipe suggestions in Azerbaijani based on what they have. The bot is designed to make recipe discovery faster, simpler, and more practical in daily use.

🤖 **Try the bot:** https://t.me/NeBishirimBot

---

## ✨ Features

* 🧺 Add and manage home ingredients
* 📷 Detect ingredients from photos with AI
* 🍽️ Get recipe suggestions based on available ingredients
* ⏱️ Filter recipes by preparation time
* 👥 Choose serving size
* 🛒 See missing ingredients before cooking
* ⭐ Save favorite recipes
* 📖 View full recipe details and cooking steps
* 🔄 Explore more recipe suggestions
* 🇦🇿 Azerbaijani-language user experience

---

## 🧠 How It Works

1. Add the ingredients you currently have at home.
2. You can also send a food photo for ingredient recognition.
3. The bot analyzes your available ingredients.
4. Suitable recipe suggestions are generated.
5. Choose preparation time and serving size.
6. See which recipes can be prepared with your current ingredients and which ones require 1–2 additional items.
7. Open a recipe to view its ingredients and preparation steps.
8. Save your favorite recipes for later.

---

## 🛠️ Tech Stack

* **Python**
* **python-telegram-bot**
* **Google Gemini API**
* **PostgreSQL**
* **FastAPI**
* **QStash**
* **GitHub Actions**

---

## 📂 Project Structure

```text
NeBishirimBot/
│
├── .github/
│   └── workflows/
│       └── tests.yml
│
├── migrations/
│   ├── 001_schema.sql
│   └── 002_shopping_and_delivery.sql
│
├── tests/
│   ├── test_database_integration.py
│   ├── test_favorites_flow.py
│   ├── test_product_features.py
│   ├── test_recipe_discovery.py
│   ├── test_recipe_quotas.py
│   └── test_worker_delivery.py
│
├── account_data.py
├── ai_features.py
├── app.py
├── basket_ui.py
├── bot.py
├── command_controls.py
├── database.py
├── delivery_store.py
├── error_handlers.py
├── favorites_store.py
├── favorites_ui.py
├── ingredient_names.py
├── migrate.py
├── pantry_store.py
├── quick_add.py
├── recipes.py
├── session_bridge.py
├── session_store.py
├── shopping_store.py
├── shopping_ui.py
├── ui_utils.py
├── DATABASE.md
├── TESTING.md
└── requirements.txt
```

---

## ⚙️ Environment Variables

Create a `.env` file in the project root and add the required values:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
GEMINI_API_KEY=your_gemini_api_key
DATABASE_URL=your_postgresql_database_url

PUBLIC_BASE_URL=your_public_https_url
TELEGRAM_WEBHOOK_SECRET=your_webhook_secret

QSTASH_TOKEN=your_qstash_token
QSTASH_CURRENT_SIGNING_KEY=your_current_signing_key
QSTASH_NEXT_SIGNING_KEY=your_next_signing_key
```

For database integration tests, you can also use:

```env
TEST_DATABASE_URL=your_test_postgresql_database_url
```

> Never commit your `.env` file, API keys, bot tokens, database passwords, or webhook secrets to GitHub.

---

## 🚀 Local Setup

Clone the repository:

```bash
git clone https://github.com/mkenan11/NeBishirimBot.git
cd NeBishirimBot
```

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it on Windows:

```bash
.venv\Scripts\activate
```

Install the dependencies:

```bash
pip install -r requirements.txt
```

Create your `.env` file and add the required environment variables.

---

## 🗄️ Database & Migrations

Database migrations are stored in the `migrations` directory.

To apply migrations:

```bash
python -B migrate.py
```

More information about the database structure and deployment behavior is available in:

[`DATABASE.md`](./DATABASE.md)

---

## 🧪 Testing

Run the offline test suite:

```bash
python -B -m unittest discover -s tests -v
```

Run PostgreSQL integration tests:

```bash
python -B -m unittest discover -s tests -p test_database_integration.py -v
```

Additional testing information is available in:

[`TESTING.md`](./TESTING.md)

GitHub Actions is configured to automatically run tests on pushes and pull requests.

---

## 🔐 Security

Sensitive credentials should always be stored as environment variables.

Never commit:

* `.env`
* Gemini API keys
* Telegram bot tokens
* database credentials
* webhook secrets
* QStash credentials

---

## 💡 Why I Built It

The project started from a simple everyday question:

> **“Evdə bunlar var, nə bişirim?”**

NeBishirimBot turns that question into a practical Telegram experience by combining AI, ingredient management, photo recognition, and recipe discovery in one place.

---

## 🔗 Links

🤖 **Telegram Bot**
https://t.me/NeBishirimBot

💻 **GitHub Repository**
https://github.com/mkenan11/NeBishirimBot

🔗 **LinkedIn**
https://www.linkedin.com/in/kanan-mammadov1/

---

## 👤 Author

**Kanan Mammadov**

If you find the project useful, feel free to ⭐ the repository.
