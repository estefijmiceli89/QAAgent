# 🤖 Jira Test Case Generator

Automatically generate comprehensive test cases for Jira tickets using Claude AI and save them to Google Docs.

## ✨ Features

- 🔍 Automatically finds tickets assigned to you in "Assigned" status
- 🤖 Uses Claude AI to generate comprehensive test cases
- 📄 Creates Google Docs with proper formatting
- 💬 Adds comments to Jira tickets with links to test case documents
- ✅ Skips tickets that already have test cases
- 🔒 Secure credential management with `.env` file

## 📋 Prerequisites

Before you start, you need:

1. **Python 3.9 or higher** installed on your computer
2. **Jira account** with API access
3. **Claude API key** (from Anthropic)
4. **Google Cloud project** with Drive and Docs APIs enabled

## 🚀 Installation

### Step 1: Clone the Repository

```bash
git clone https://github.com/YOUR-USERNAME/jira-test-case-generator.git
cd jira-test-case-generator
```

### Step 2: Install Python Dependencies

```bash
pip install -r requirements.txt
```

### Step 3: Set Up Jira API Token

1. Go to https://id.atlassian.com/manage-profile/security/api-tokens
2. Click **Create API token**
3. Give it a name (e.g., "Test Case Generator")
4. Copy the token (you'll need it in Step 6)

### Step 4: Set Up Claude API Key

1. Go to https://console.anthropic.com/
2. Sign up or log in
3. Go to **API Keys** section
4. Create a new API key
5. Copy the key (you'll need it in Step 6)

### Step 5: Set Up Google Cloud Credentials

1. Go to https://console.cloud.google.com/
2. Create a new project or select an existing one
3. Enable these APIs:
   - Google Drive API
   - Google Docs API
4. Go to **APIs & Services > Credentials**
5. Click **Create Credentials > OAuth client ID**
6. Choose **Desktop app** as application type
7. Download the JSON file and save it as `credentials.json` in the project folder

### Step 6: Configure Environment Variables

1. Copy the example configuration file:
   ```bash
   cp .env.example .env
   ```

2. Open `.env` in a text editor and fill in your values:
   ```
   JIRA_URL=https://fpsinc.atlassian.net
   JIRA_EMAIL=your-email@example.com
   JIRA_API_TOKEN=your_jira_api_token_here
   JIRA_PROJECT=CCAI
   CLAUDE_API_KEY=your_claude_api_key_here
   GOOGLE_DRIVE_FOLDER_ID=1MazY7ZEo6_WUIunO7TJ4e2ZtAqT7UX9y
   ```

**How to get the Google Drive Folder ID:**
- Open your Google Drive folder
- The URL will look like: `https://drive.google.com/drive/folders/FOLDER_ID_HERE`
- Copy the `FOLDER_ID_HERE` part

## 🎯 Usage

Run the script:

```bash
python generate_test_cases.py
```

### What happens:

1. ✅ The script connects to Jira and searches for tickets assigned to you in "Assigned" status
2. 🔍 For each ticket, it checks if there's already a Google Doc link in the comments
3. 🤖 If no link exists, it generates test cases using Claude AI
4. 📄 Creates a Google Doc with the ticket ID as the name
5. 📁 Saves the document to your specified Google Drive folder
6. 💬 Adds a comment to the Jira ticket with the Google Doc link

### First Run - Google Authentication

The first time you run the script, it will:
1. Open your browser for Google authentication
2. Ask you to authorize the app
3. Save your credentials in `token.json` for future runs

## 📁 Project Structure

```
jira-test-case-generator/
├── generate_test_cases.py    # Main script
├── prompt_maestro.txt         # Test case generation template
├── requirements.txt           # Python dependencies
├── .env.example              # Configuration template
├── .env                      # Your actual configuration (DO NOT COMMIT)
├── credentials.json          # Google OAuth credentials (DO NOT COMMIT)
├── token.json               # Google OAuth token (DO NOT COMMIT)
├── .gitignore               # Files to ignore in git
└── README.md                # This file
```

## 🔧 Customization

### Modify the Prompt Template

Edit `prompt_maestro.txt` to customize how test cases are generated.

### Change the Claude Model

Edit line 126 in `generate_test_cases.py`:
```python
model="claude-sonnet-4-20250514",  # Change to claude-opus-4-5-20251101 for better quality
```

### Filter Different Ticket Statuses

Edit line 95 in `generate_test_cases.py`:
```python
jql = f'project = {JIRA_PROJECT} AND assignee = currentUser() AND status = "Assigned"'
```

Change `status = "Assigned"` to whatever status you want (e.g., `"In Progress"`, `"To Do"`).

## ⚠️ Troubleshooting

### "Module not found" error
```bash
pip install -r requirements.txt
```

### "Invalid credentials" for Jira
- Check your Jira URL (should include https://)
- Verify your email and API token
- Make sure your Jira account has access to the project

### Google authentication fails
- Delete `token.json` and try again
- Make sure you downloaded `credentials.json` correctly
- Check that Drive and Docs APIs are enabled in Google Cloud Console

### Claude API errors
- Verify your API key is correct
- Check you have available credits in your Anthropic account
- Visit https://console.anthropic.com/ to check your usage

## 💰 Cost Estimate

**Claude API Usage:**
- Model: Claude Sonnet 4
- Cost: ~$0.01-0.03 per test case generation
- For 20 tickets/day: ~$0.20-0.60/day

**Your existing $20/month plan should cover moderate usage.**

## 🔒 Security Notes

- **Never commit** `.env`, `credentials.json`, or `token.json` to GitHub
- These files are in `.gitignore` to protect your credentials
- Each user should configure their own credentials

## 🤝 Contributing

Feel free to fork this repository and submit pull requests!

## 📝 License

MIT License - feel free to use this for any purpose

## 👤 Author

Created for QA teams who want to automate test case generation.

## 🆘 Support

If you encounter issues:
1. Check the troubleshooting section above
2. Open an issue on GitHub
3. Make sure all prerequisites are installed correctly

---

**Happy Testing! 🧪**
