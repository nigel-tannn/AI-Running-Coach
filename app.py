import streamlit as st
import xml.etree.ElementTree as ET
import pandas as pd
import google.generativeai as genai
import sqlite3
import json
from datetime import datetime, timezone, timedelta
import os
import altair as alt
from PIL import Image
import hashlib

# ----------------------------
# Configure Gemini API
# ----------------------------
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
except (KeyError, FileNotFoundError):
    API_KEY = os.environ.get("GEMINI_API_KEY", "")

# ----------------------------
# Authentication Helpers
# ----------------------------
def hash_password(password):
    """Hash a password for storing."""
    return hashlib.sha256(password.encode()).hexdigest()

def verify_user(username, password):
    """Verify a user's login credentials."""
    with sqlite3.connect('coach.db') as conn:
        c = conn.cursor()
        c.execute("SELECT id, password FROM users WHERE username = ?", (username,))
        row = c.fetchone()
        if row and row[1] == hash_password(password):
            return row[0] # Return user_id
        return None

def create_user(username, password):
    """Create a new user account."""
    with sqlite3.connect('coach.db') as conn:
        c = conn.cursor()
        try:
            c.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hash_password(password)))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False # Username already exists

# ----------------------------
# Database Setup
# ----------------------------

def init_db():
    """Initialize the SQLite database for the multi-tenant app."""
    with sqlite3.connect('coach.db') as conn:
        c = conn.cursor()
        
        # 1. Create Users Table
        c.execute('''CREATE TABLE IF NOT EXISTS users
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                      username TEXT UNIQUE, 
                      password TEXT)''')
                      
        # Ensure Default User (Nigel) exists
        c.execute("SELECT id FROM users WHERE username = 'Nigel'")
        if not c.fetchone():
            c.execute("INSERT INTO users (username, password) VALUES (?, ?)", ("Nigel", hash_password("nigel123")))

        # 2. Initialize Data Tables with user_id
        c.execute('''CREATE TABLE IF NOT EXISTS runs
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                      user_id INTEGER,
                      date TEXT, distance REAL, duration REAL, 
                      avg_hr REAL, pace REAL, run_type TEXT, insight TEXT)''')
                      
        c.execute('''CREATE TABLE IF NOT EXISTS macro_plan
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, plan_text TEXT)''')
                     
        c.execute('''CREATE TABLE IF NOT EXISTS micro_plan
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, plan_json TEXT)''')
                     
        c.execute('''CREATE TABLE IF NOT EXISTS user_profile
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, goal TEXT, race_date TEXT, available_days TEXT)''')

        conn.commit()

# --- Profile Database Functions ---
def get_user_profile(user_id):
    with sqlite3.connect('coach.db') as conn:
        c = conn.cursor()
        c.execute("SELECT goal, race_date, available_days FROM user_profile WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        if not row:
            # Create completely empty default profile for new users
            c.execute("INSERT INTO user_profile (user_id, goal, race_date, available_days) VALUES (?, ?, ?, ?)",
                      (user_id, "", "", "[]"))
            conn.commit()
            return ("", "", "[]")
        return row

def update_user_profile(user_id, goal, race_date, available_days):
    with sqlite3.connect('coach.db') as conn:
        c = conn.cursor()
        date_str = race_date.strftime("%Y-%m-%d") if race_date else ""
        days_str = json.dumps(available_days)
        
        c.execute("SELECT id FROM user_profile WHERE user_id = ?", (user_id,))
        if c.fetchone():
            c.execute("UPDATE user_profile SET goal = ?, race_date = ?, available_days = ? WHERE user_id = ?",
                      (goal, date_str, days_str, user_id))
        else:
            c.execute("INSERT INTO user_profile (user_id, goal, race_date, available_days) VALUES (?, ?, ?, ?)",
                      (user_id, goal, date_str, days_str))
        conn.commit()

# --- Run History Database Functions ---
def run_exists(user_id, date):
    with sqlite3.connect('coach.db') as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM runs WHERE user_id = ? AND date = ?", (user_id, date))
        return c.fetchone() is not None

def save_run(user_id, date, distance, duration, avg_hr, pace, run_type):
    with sqlite3.connect('coach.db') as conn:
        c = conn.cursor()
        c.execute("INSERT INTO runs (user_id, date, distance, duration, avg_hr, pace, run_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (user_id, date, distance, duration, avg_hr, pace, run_type))
        conn.commit()
        return c.lastrowid

def update_run_insight(user_id, run_id, insight_text):
    with sqlite3.connect('coach.db') as conn:
        c = conn.cursor()
        c.execute("UPDATE runs SET insight = ? WHERE id = ? AND user_id = ?", (insight_text, int(run_id), user_id))
        conn.commit()

def update_run_type(user_id, run_id, new_run_type):
    with sqlite3.connect('coach.db') as conn:
        c = conn.cursor()
        c.execute("UPDATE runs SET run_type = ? WHERE id = ? AND user_id = ?", (new_run_type, int(run_id), user_id))
        conn.commit()

def get_run_history(user_id, limit=None):
    with sqlite3.connect('coach.db') as conn:
        query = f"SELECT id, date, distance, duration, avg_hr, pace, run_type, insight FROM runs WHERE user_id = {user_id} ORDER BY date DESC"
        if limit:
            query += f" LIMIT {limit}"
        df = pd.read_sql_query(query, conn)
    return df

def delete_run(user_id, run_id):
    with sqlite3.connect('coach.db') as conn:
        c = conn.cursor()
        c.execute("DELETE FROM runs WHERE id = ? AND user_id = ?", (int(run_id), user_id))
        conn.commit()

# --- Plan Database Functions ---
def get_macro_plan(user_id):
    with sqlite3.connect('coach.db') as conn:
        df = pd.read_sql_query(f"SELECT plan_text FROM macro_plan WHERE user_id = {user_id}", conn)
        if not df.empty:
            return df['plan_text'].iloc[0]
        return None

def save_macro_plan(user_id, plan_text):
    with sqlite3.connect('coach.db') as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM macro_plan WHERE user_id = ?", (user_id,))
        if c.fetchone():
            c.execute("UPDATE macro_plan SET plan_text = ? WHERE user_id = ?", (plan_text, user_id))
        else:
            c.execute("INSERT INTO macro_plan (user_id, plan_text) VALUES (?, ?)", (user_id, plan_text))
        conn.commit()
        
def get_micro_plan(user_id):
    with sqlite3.connect('coach.db') as conn:
        df = pd.read_sql_query(f"SELECT plan_json FROM micro_plan WHERE user_id = {user_id}", conn)
        if not df.empty:
            return json.loads(df['plan_json'].iloc[0])
        return None

def save_micro_plan(user_id, plan_data):
    with sqlite3.connect('coach.db') as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM micro_plan WHERE user_id = ?", (user_id,))
        if c.fetchone():
            c.execute("UPDATE micro_plan SET plan_json = ? WHERE user_id = ?", (json.dumps(plan_data), user_id))
        else:
            c.execute("INSERT INTO micro_plan (user_id, plan_json) VALUES (?, ?)", (user_id, json.dumps(plan_data)))
        conn.commit()

# ----------------------------
# Helper Functions
# ----------------------------

def format_pace(decimal_pace):
    if pd.isna(decimal_pace):
        return "0:00"
    minutes = int(decimal_pace)
    seconds = int((decimal_pace - minutes) * 60)
    return f"{minutes}:{seconds:02d}"

def format_duration(minutes):
    if pd.isna(minutes):
        return "0m"
    h = int(minutes // 60)
    m = int(minutes % 60)
    if h > 0:
        return f"{h}h {m}m"
    else:
        return f"{m}m"

# ----------------------------
# Parse TCX File
# ----------------------------

def parse_tcx(file):
    tree = ET.parse(file)
    root = tree.getroot()
    ns = {'ns': 'http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2'}
    
    trackpoints = []
    laps_data = []
    
    for lap_idx, lap in enumerate(root.findall(".//ns:Lap", ns)):
        lap_dist_node = lap.find("ns:DistanceMeters", ns)
        lap_time_node = lap.find("ns:TotalTimeSeconds", ns)
        lap_hr_node = lap.find("ns:AverageHeartRateBpm/ns:Value", ns)
        
        lap_dist = float(lap_dist_node.text) if lap_dist_node is not None else 0
        lap_time = float(lap_time_node.text) if lap_time_node is not None else 0
        lap_hr = int(lap_hr_node.text) if lap_hr_node is not None else None
        
        if lap_dist > 0:
            laps_data.append({
                "lap": lap_idx + 1,
                "distance": lap_dist / 1000,
                "duration": lap_time / 60,
                "avg_hr": lap_hr if lap_hr else 0,
                "pace": (lap_time / 60) / (lap_dist / 1000)
            })
            
        for tp in lap.findall(".//ns:Trackpoint", ns):
            time_node = tp.find("ns:Time", ns)
            dist_node = tp.find("ns:DistanceMeters", ns)
            hr_node = tp.find(".//ns:HeartRateBpm/ns:Value", ns)
            
            if time_node is not None and dist_node is not None:
                time_str = time_node.text.replace('Z', '+00:00')
                dt_utc = datetime.fromisoformat(time_str)
                dt_sgt = dt_utc.astimezone(timezone(timedelta(hours=8)))
                
                trackpoints.append({
                    "time": dt_sgt,
                    "distance": float(dist_node.text),
                    "hr": int(hr_node.text) if hr_node is not None else None
                })
            
    df = pd.DataFrame(trackpoints)
    if not df.empty:
        df["distance_km"] = df["distance"] / 1000
        
    laps_df = pd.DataFrame(laps_data)
    return df, laps_df

def compute_metrics(df):
    if df.empty:
        return None
    distance = df["distance_km"].max()
    duration_secs = (df["time"].iloc[-1] - df["time"].iloc[0]).total_seconds()
    duration_mins = duration_secs / 60
    avg_hr = df["hr"].mean()
    pace = duration_mins / distance if distance > 0 else 0
    start_time = df["time"].iloc[0]
    
    return {
        "date": start_time.strftime("%Y-%m-%d %H:%M"),
        "distance": round(distance, 2),
        "duration": round(duration_mins, 2),
        "avg_hr": round(avg_hr, 1),
        "pace": round(pace, 2),
        "formatted_pace": format_pace(pace)
    }

def generate_detailed_context(metrics, laps_df, run_type):
    context = f"Run Type Categorization: {run_type}\n"
    context += f"Overall Distance: {metrics['distance']} km\n"
    context += f"Overall Duration: {metrics['duration']} min\n"
    context += f"Overall Pace: {metrics['formatted_pace']} min/km\n"
    context += f"Overall Avg HR: {metrics['avg_hr']} bpm\n\n"
    
    if not laps_df.empty:
        if run_type in ['Interval', 'Tempo']:
            context += "Lap Breakdown (Assess rep consistency, pace targets, and HR recovery between reps):\n"
        else:
            context += "Kilometer/Lap Splits (Assess cardiovascular/HR drift over time and pace consistency):\n"
            
        for _, row in laps_df.iterrows():
            pace_str = format_pace(row['pace'])
            hr_str = f"{int(row['avg_hr'])} bpm" if row['avg_hr'] > 0 else "N/A"
            context += f"- Lap {int(row['lap'])}: {row['distance']:.2f}km in {row['duration']:.2f}m (Pace: {pace_str}), Avg HR: {hr_str}\n"
    return context

# ----------------------------
# AI Generation Functions
# ----------------------------

def generate_broad_plan_ai(user_goal, race_date, weeks_to_race, available_days, run_history_df):
    genai.configure(api_key=API_KEY)
    model = genai.GenerativeModel("gemini-2.5-flash")
    
    history_str = run_history_df.to_string(index=False) if not run_history_df.empty else "No previous data."
    avail_days_str = ", ".join(available_days) if available_days else "None"
    goal_str = user_goal if user_goal else "Improve overall fitness"
    
    if race_date:
        race_info = f"- Race Date: {race_date} ({weeks_to_race:.1f} weeks away)"
        task_instruction = f"1. Break the remaining {weeks_to_race:.1f} weeks down into distinct training blocks (e.g., Base, Build, Peak, Taper)."
    else:
        race_info = "- Race Date: Not set"
        task_instruction = "1. Break the next 12 weeks down into distinct training blocks based on general fitness progression."
    
    prompt = f"""
    You are an elite endurance running coach.
    
    User Profile:
    - Goal: "{goal_str}"
    {race_info}
    - Available Running Days: {avail_days_str}
    
    Recent History (for fitness baseline):
    {history_str}
    
    Task:
    Provide a comprehensive, high-level Macrocycle Training Plan.
    {task_instruction}
    2. For each block, specify the primary focus, target weekly mileage (ramping up safely), and key workout types.
    3. Ensure the principles of progressive overload are applied safely (no more than 10-15% weekly volume increase) to avoid overtraining.
    4. Respond in beautifully formatted Markdown with headers, bullet points, and clear distinctions between phases. Do not output JSON.
    """
    response = model.generate_content(prompt)
    return response.text

def generate_historical_insight(detailed_run_context, uploaded_images, run_date):
    genai.configure(api_key=API_KEY)
    model = genai.GenerativeModel("gemini-2.5-flash")
    
    prompt = f"""
    You are an elite endurance running coach. 
    Analyze the following run breakdown from {run_date}:
    {detailed_run_context}
    
    Provide a detailed, qualitative analysis of this run. Discuss pacing consistency, heart rate drift, effort levels, and recovery. 
    Heavily incorporate the provided screenshots (e.g., lap paces, HR graphs) to extract deeper insights.
    Respond ONLY with the qualitative analysis text. Do not use JSON.
    """
    contents = [prompt]
    if uploaded_images:
        for img_file in uploaded_images:
            try:
                img = Image.open(img_file)
                contents.append(img)
            except Exception: pass
    response = model.generate_content(contents)
    return response.text

def update_training_plan(detailed_run_context, run_history_df, user_goal, available_days, current_phase, weeks_to_race, uploaded_images=None, screenshot_run_date=None, latest_run_type="Easy", latest_run_date_str="Unknown"):
    genai.configure(api_key=API_KEY)
    model = genai.GenerativeModel("gemini-2.5-flash")
    
    history_str = run_history_df.to_string(index=False) if not run_history_df.empty else "No previous data."
    
    sgt_now = datetime.now(timezone(timedelta(hours=8)))
    today_name = sgt_now.strftime("%A, %B %d")
    today_date_str = sgt_now.strftime("%Y-%m-%d")
    
    # Start the 7-day plan from today (day 0)
    next_7_days = [(sgt_now + timedelta(days=i)).strftime("%A, %B %d") for i in range(0, 7)]
    next_7_days_str = ", ".join(next_7_days)
    
    all_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    rest_days = [d for d in all_days if d not in available_days]
    
    avail_days_str = ", ".join(available_days) if available_days else "None"
    rest_days_str = ", ".join(rest_days) if rest_days else "None"
    
    goal_str = user_goal if user_goal else "Improve overall fitness"
    if "No race date set" in current_phase:
        phase_str = f"Their current training phase is: **{current_phase}**."
    else:
        phase_str = f"They are currently {weeks_to_race:.1f} weeks away from their race day, putting them in the **{current_phase}**."
        
    # Correctly identify if the latest logged run happened TODAY or in the PAST
    latest_run_was_today = latest_run_date_str.startswith(today_date_str)
    
    if latest_run_was_today:
        run_timing_context = f"Here is the detailed breakdown of the run they just completed TODAY ({today_name}):\n{detailed_run_context}"
        today_instruction = f"- The user HAS ALREADY COMPLETED a run today ({latest_run_type}). You MUST assign 'Rest / Completed' for today's plan entry ({today_name}) and schedule the next active run in the sequence for a subsequent available day."
    else:
        run_timing_context = f"Here is the detailed breakdown of their MOST RECENT run (completed on {latest_run_date_str}, which is NOT today):\n{detailed_run_context}"
        today_instruction = f"- The user HAS NOT run today. Schedule the NEXT active run in the sequence starting from today ({today_name}), provided today is one of their available running days. If today is not an available day, assign 'Rest' and schedule the active run for the next available day."
    
    prompt = f"""
    You are an elite endurance running coach. The user's ultimate goal is: "{goal_str}".
    {phase_str}
    
    Here is their recent running history (last 10 runs):
    {history_str}
    
    {run_timing_context}
    """
    
    if uploaded_images and screenshot_run_date:
        prompt += f"\n[NOTE TO AI: The user provided screenshots corresponding to their run on {screenshot_run_date}. Please heavily incorporate these visuals into your qualitative analysis.]\n"

    prompt += f"""
    Task 1: Generate a Qualitative Insight
    Provide a detailed, qualitative analysis of the latest run. Discuss pacing consistency, heart rate drift, effort levels, and recovery. 
    
    Task 2: Generate a 7-Day Training Schedule (Microcycle)
    - The exact next 7 days are: {next_7_days_str}. Use these EXACT strings for the "date" field.
    {today_instruction}
    - CRITICAL SCHEDULE CONSTRAINTS: 
      1. Available running days: {avail_days_str}. You MUST assign "Rest" (0 km) on {rest_days_str}.
      2. WORKOUT SEQUENCE: The ideal weekly sequence of active runs is: Easy Run -> Interval -> Tempo -> Long Run.
         The user's most recent run was: **{latest_run_type}**. Sequence the NEXT active day based on this cycle.
      3. PROGRESSIVE OVERLOAD & OVERTRAINING PREVENTION: Look at their recent historical distances. Do NOT increase total weekly mileage by more than 10-15%. Prioritize recovery if their HR data indicates fatigue. Scale the intensity appropriately.
      4. EXTREMELY DETAILED GUIDANCE: For active runs, provide highly detailed Markdown guidance (`workout_details`). Must include headers for: Goal, Warm-up, Main Set (reps, exact segment pace targets), Cool-down, and Execution Cues.
    
    Respond ONLY with a valid JSON object matching this exact schema:
    {{
      "qualitative_insight": "Your detailed, qualitative analysis paragraph...",
      "plan": [
        {{
          "date": "Monday, March 16",
          "type": "Interval",
          "distance_km": 7,
          "workout_details": "### Goal\\nImprove VO2max.\\n\\n### Warm-up\\n- 2 km easy (6:30-6:50/km)\\n- HR < 150\\n\\n### Main Set\\n- 6 x 400m\\n- **Target pace:** 4:15-4:20/km\\n- **Recovery:** 90 sec easy jog\\n\\n### Cool-down\\n- 1.5 km easy (6:30-6:50/km)\\n\\n### Key Execution Cues\\n- Quick cadence (~180 spm)"
        }}
      ]
    }}
    Make sure the 'plan' array has exactly 7 items (one for each of the next 7 days).
    """
    
    contents = [prompt]
    if uploaded_images:
        for img_file in uploaded_images:
            try:
                img = Image.open(img_file)
                contents.append(img)
            except Exception: pass 

    response = model.generate_content(
        contents,
        generation_config={"response_mime_type": "application/json"}
    )
    return json.loads(response.text)


# ----------------------------
# Streamlit UI & Core Logic
# ----------------------------

st.set_page_config(page_title="AI Running Coach", page_icon="🏃‍♂️", layout="wide")

# Inject Custom CSS
st.markdown("""
<style>
    .stApp { font-family: 'Inter', sans-serif; }
    h1, h2, h3 { font-weight: 700 !important; }
    .insight-box {
        background: #f8fafc; padding: 24px; border-radius: 12px;
        border-left: 8px solid #3b82f6; margin-bottom: 24px;
        color: #0f172a !important; font-size: 1.1rem; line-height: 1.6;
    }
    .streamlit-expanderHeader { font-weight: 600 !important; font-size: 1.1rem !important; }
    div[data-testid="stExpander"] { border: 1px solid rgba(150, 150, 150, 0.2); border-radius: 10px; margin-bottom: 12px; }
    div[data-testid="stMetricValue"] { color: #3b82f6 !important; font-weight: 800; font-size: 2rem;}
    .css-9ycgxx { display: none; }
</style>
""", unsafe_allow_html=True)

# Initialize database
init_db()

# --- AUTHENTICATION LAYER ---
if 'user_id' not in st.session_state:
    st.title("🏃‍♂️ AI Dynamic Running Coach")
    st.write("Welcome! Please log in to access your personal training dashboard.")
    
    tab_login, tab_signup = st.tabs(["Log In", "Sign Up"])
    
    with tab_login:
        with st.form("login_form"):
            l_username = st.text_input("Username")
            l_password = st.text_input("Password", type="password")
            if st.form_submit_button("Login"):
                user_id = verify_user(l_username, l_password)
                if user_id:
                    st.session_state['user_id'] = user_id
                    st.session_state['username'] = l_username
                    st.rerun()
                else:
                    st.error("Invalid username or password.")
                    
    with tab_signup:
        with st.form("signup_form"):
            s_username = st.text_input("Choose Username")
            s_password = st.text_input("Choose Password", type="password")
            if st.form_submit_button("Create Account"):
                if s_username and s_password:
                    if create_user(s_username, s_password):
                        st.success("Account created successfully! Please log in from the other tab.")
                    else:
                        st.error("Username already exists. Please choose a different one.")
                else:
                    st.warning("Please fill out both fields.")
    
    st.stop() # Halt execution if not logged in

# --- MAIN APP (Authenticated) ---
USER_ID = st.session_state['user_id']

# Load the stored 7-Day Plan from DB into session state on app start
if 'current_plan' not in st.session_state:
    stored_plan = get_micro_plan(USER_ID)
    if stored_plan:
        st.session_state['current_plan'] = stored_plan

col_title, col_logout = st.columns([5, 1])
with col_title:
    st.title("🏃‍♂️ AI Dynamic Running Coach")
with col_logout:
    st.write("")
    if st.button("🚪 Logout"):
        st.session_state.clear()
        st.rerun()

# Display current SGT time
sgt_now = datetime.now(timezone(timedelta(hours=8)))
sgt_time_str = sgt_now.strftime("%A, %B %d, %Y - %I:%M %p (SGT)")
st.caption(f"🕒 **Current Time:** {sgt_time_str} | 👤 **Logged in as:** {st.session_state['username']}")

# --- Sidebar & Persistent Profile Logic ---
profile_data = get_user_profile(USER_ID)
db_goal, db_race_date_str, db_avail_days_str = profile_data
db_race_date = datetime.strptime(db_race_date_str, "%Y-%m-%d").date() if db_race_date_str else None
db_avail_days = json.loads(db_avail_days_str) if db_avail_days_str else []

st.sidebar.header("🎯 Goal & Timeline")
user_goal = st.sidebar.text_area("What is your running goal?", value=db_goal, placeholder="e.g. Run a Sub-50 min 10k")
race_date = st.sidebar.date_input("Race Date", value=db_race_date)

st.sidebar.subheader("🗓️ Availability")
available_days = st.sidebar.multiselect(
    "Select your available running days:",
    ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    default=db_avail_days
)

if not user_goal:
    st.sidebar.warning("Please set a running goal.")
if not race_date:
    st.sidebar.warning("Please set a target race date.")
if not available_days:
    st.sidebar.warning("Please select at least one available running day.")

# Auto-save changes to the profile
if user_goal != db_goal or race_date != db_race_date or available_days != db_avail_days:
    update_user_profile(USER_ID, user_goal, race_date, available_days)

# Calculate Training Phase based on weeks to race
if race_date:
    weeks_to_race = (race_date - sgt_now.date()).days / 7
    if weeks_to_race > 8:
        current_phase = "Base Phase (Building aerobic capacity & mileage)"
    elif weeks_to_race > 4:
        current_phase = "Build Phase (Increasing intensity & race-specific pace)"
    elif weeks_to_race > 2:
        current_phase = "Peak Phase (Maximum race-specific fitness)"
    elif weeks_to_race >= 0:
        current_phase = "Taper Phase (Reducing fatigue, maintaining sharpness)"
    else:
        current_phase = "Recovery/Post-Race"
else:
    weeks_to_race = 0.0
    current_phase = "General Training (No race date set)"

# --- Tabs ---
# Determine if the user is the superuser (Nigel)
is_admin = st.session_state.get('username') == 'Nigel'

tab_names = ["📤 Upload", "📅 7-Day Plan", "🗺️ Broad Plan", "📊 History", "⚙️ Manage"]
if is_admin:
    tab_names.append("🛠️ Admin DB")

tabs = st.tabs(tab_names)
tab1, tab2, tab3, tab4, tab5 = tabs[0], tabs[1], tabs[2], tabs[3], tabs[4]
if is_admin:
    tab_admin = tabs[5]

with tab1:
    st.markdown("### 1️⃣ Upload Activity Data")
    # Removing 'type' to bypass iOS iCloud restriction on .tcx
    uploaded_files = st.file_uploader("Upload Garmin TCX Files", accept_multiple_files=True, label_visibility="collapsed")
    
    screenshot_files = []
    target_screenshot_run_date = None
    
    if uploaded_files:
        all_runs_data = []
        duplicates_db = []
        duplicates_batch = []
        seen_in_batch = set()
        
        with st.spinner("Parsing TCX data & extracting laps..."):
            for file in uploaded_files:
                # Backend validation for file type
                if not file.name.lower().endswith('.tcx'):
                    st.warning(f"⚠️ Skipped '{file.name}' - Only .tcx files are supported for activity data.")
                    continue
                    
                try:
                    df, laps_df = parse_tcx(file)
                except Exception:
                    st.error(f"❌ Failed to parse '{file.name}'. Ensure it is a valid XML/TCX file.")
                    continue
                    
                metrics = compute_metrics(df)
                if metrics:
                    run_date = metrics['date']
                    
                    if run_exists(USER_ID, run_date):
                        duplicates_db.append(f"{file.name} ({run_date})")
                        continue
                        
                    if run_date in seen_in_batch:
                        duplicates_batch.append(file.name)
                        continue
                        
                    seen_in_batch.add(run_date)
                    all_runs_data.append({
                        "file_name": file.name,
                        "metrics": metrics,
                        "laps_df": laps_df
                    })
        
        if duplicates_db:
            st.warning(f"⚠️ Skipped {len(duplicates_db)} file(s) because they are already saved in your history: {', '.join(duplicates_db)}")
        if duplicates_batch:
            st.warning(f"⚠️ Skipped {len(duplicates_batch)} identical file(s) uploaded within this batch.")
            
        if all_runs_data:
            all_runs_data = sorted(all_runs_data, key=lambda x: x['metrics']['date'], reverse=True)
            st.success(f"✅ {len(all_runs_data)} new run(s) parsed successfully!")
            
            st.markdown("### 2️⃣ Review & Categorize")
            st.info("Tag your recent runs so the AI can sequence your upcoming workouts correctly (Easy → Interval → Tempo → Long Run).")
            
            with st.container(border=True):
                for i, run in enumerate(all_runs_data):
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        st.markdown(f"**{run['file_name']}**<br><span style='color:gray; font-size:0.9em'>{run['metrics']['date']} | {run['metrics']['distance']} km | {run['metrics']['duration']} min</span>", unsafe_allow_html=True)
                    with col2:
                        run['run_type'] = st.selectbox(
                            "Run Type", 
                            options=["Easy", "Interval", "Tempo", "Long Run"], 
                            key=f"type_{i}", 
                            label_visibility="collapsed"
                        )
                    if i < len(all_runs_data) - 1:
                        st.divider()
            
            st.markdown("### 3️⃣ Enrich Insights (Optional)")
            with st.expander("📸 Attach Lap Paces / HR Charts"):
                st.write("Upload screenshots from Garmin Connect or Coros to give the AI deeper visual context.")
                screenshot_files = st.file_uploader("Upload Screenshots", type=['png', 'jpg', 'jpeg'], accept_multiple_files=True, label_visibility="collapsed")
                
                if screenshot_files:
                    run_options_for_images = {f"{r['file_name']} ({r['metrics']['date']})": r for r in all_runs_data}
                    selected_run_for_images = st.selectbox("Assign these screenshots to:", list(run_options_for_images.keys()))
                    target_screenshot_run_date = run_options_for_images[selected_run_for_images]['metrics']['date']

            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("🚀 Log Run(s) & Generate Plan", type="primary", width="stretch", disabled=len(available_days)==0):
                if not API_KEY:
                    st.error("API Key is missing! Please set GEMINI_API_KEY in your .streamlit/secrets.toml or environment variables.")
                else:
                    latest_run_id = None
                    
                    for run in all_runs_data:
                        m = run['metrics']
                        run_id = save_run(USER_ID, m['date'], m['distance'], m['duration'], m['avg_hr'], m['pace'], run['run_type'])
                        if run == all_runs_data[0]:
                            latest_run_id = run_id
                    
                    history_df = get_run_history(USER_ID, limit=15)
                    latest_run = all_runs_data[0] 
                    detailed_context = generate_detailed_context(latest_run['metrics'], latest_run['laps_df'], latest_run['run_type'])
                    
                    with st.spinner("Coach AI is analyzing your splits and structuring your next block..."):
                        try:
                            coach_response = update_training_plan(
                                detailed_run_context=detailed_context, 
                                run_history_df=history_df, 
                                user_goal=user_goal, 
                                available_days=available_days, 
                                current_phase=current_phase, 
                                weeks_to_race=weeks_to_race, 
                                uploaded_images=screenshot_files, 
                                screenshot_run_date=target_screenshot_run_date, 
                                latest_run_type=latest_run['run_type'],
                                latest_run_date_str=latest_run['metrics']['date']
                            )
                            
                            if latest_run_id and 'qualitative_insight' in coach_response:
                                update_run_insight(USER_ID, latest_run_id, coach_response['qualitative_insight'])
                            
                            st.session_state['current_plan'] = coach_response['plan']
                            save_micro_plan(USER_ID, coach_response['plan'])
                            
                            st.success("Training Plan & Insights Updated! Check the 'My Training Plan' tab.")
                        except Exception as e:
                            st.error(f"Error communicating with AI: {e}")

with tab2:
    st.header("📅 Your 7-Day Schedule")
    
    col1, col2 = st.columns([3, 1])
    with col1:
        st.write(f"Here is your dynamically generated plan. It is tailored to the **{current_phase.split('(')[0].strip()}** to gradually build your fitness safely.")
    with col2:
        if st.button("🔄 Refresh Plan", type="primary", disabled=len(available_days)==0, width="stretch"):
            if not API_KEY:
                st.error("API Key is missing!")
            else:
                history_df = get_run_history(USER_ID, limit=15)
                if history_df.empty:
                    st.warning("You need to log at least one run in the history before generating a plan.")
                else:
                    latest_run_row = history_df.iloc[0]
                    pseudo_metrics = {
                        'distance': latest_run_row['distance'],
                        'duration': latest_run_row['duration'],
                        'avg_hr': latest_run_row['avg_hr'],
                        'formatted_pace': format_pace(latest_run_row['pace'])
                    }
                    detailed_context = generate_detailed_context(pseudo_metrics, pd.DataFrame(), latest_run_row['run_type'])
                    
                    with st.spinner("Coach AI is scaling your mileage and generating your updated schedule..."):
                        try:
                            coach_response = update_training_plan(
                                detailed_run_context=detailed_context, 
                                run_history_df=history_df, 
                                user_goal=user_goal, 
                                available_days=available_days, 
                                current_phase=current_phase, 
                                weeks_to_race=weeks_to_race, 
                                uploaded_images=None, 
                                screenshot_run_date=None, 
                                latest_run_type=latest_run_row['run_type'],
                                latest_run_date_str=latest_run_row['date']
                            )
                            
                            if 'qualitative_insight' in coach_response:
                                update_run_insight(USER_ID, latest_run_row['id'], coach_response['qualitative_insight'])
                                
                            st.session_state['current_plan'] = coach_response['plan']
                            save_micro_plan(USER_ID, coach_response['plan'])
                            st.rerun() 
                        except Exception as e:
                            st.error(f"Error communicating with AI: {e}")
    
    st.divider()

    if 'current_plan' in st.session_state and st.session_state['current_plan']:
        plan = st.session_state['current_plan']
        for day in plan:
            icon = "🏃"
            if "Rest" in day['type']: icon = "🛋️"
            elif "Interval" in day['type']: icon = "⚡"
            elif "Tempo" in day['type']: icon = "🔥"
            elif "Long" in day['type']: icon = "🗺️"
            
            display_date = day.get('date', f"Day {day.get('day', '?')}")
            with st.expander(f"{display_date} - {icon} {day['type']} ({day.get('distance_km', 0)} km)"):
                if day['type'] == 'Rest' or 'Completed' in day['type']:
                    st.write(day.get('workout_details', 'Rest and recover. Focus on hydration, adequate sleep, and light mobility if needed.'))
                else:
                    st.markdown(day.get('workout_details', ''))
    else:
        st.warning("Upload a run or click 'Refresh Plan' to generate your dynamic training plan.")

with tab3:
    st.header("🗺️ Broad Training Plan (Macrocycle)")
    
    if race_date:
        st.write(f"**Target Race Date:** {race_date.strftime('%A, %B %d, %Y')} ({weeks_to_race:.1f} weeks away)")
    else:
        st.write("**Target Race Date:** Not set")
    st.info(f"📍 **Current Stage:** {current_phase}")
    
    st.write("Generate a week-by-week overview of your entire training block leading up to race day to understand how your mileage and intensity will gradually ramp up safely.")
    
    if st.button("🗺️ Generate Broad Plan", type="primary"):
        if not API_KEY:
            st.error("API Key is missing!")
        else:
            with st.spinner("AI is crafting your long-term periodization strategy..."):
                try:
                    history_df = get_run_history(USER_ID, limit=20)
                    macro_plan_text = generate_broad_plan_ai(user_goal, race_date, weeks_to_race, available_days, history_df)
                    save_macro_plan(USER_ID, macro_plan_text)
                    st.success("Macrocycle Plan Generated!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error generating broad plan: {e}")
                    
    st.divider()
    saved_macro_plan = get_macro_plan(USER_ID)
    if saved_macro_plan:
        st.markdown(saved_macro_plan)
    else:
        st.info("No Broad Plan generated yet. Click the button above to create one.")

with tab4:
    st.header("📊 Run History & Insights")
    history_df = get_run_history(USER_ID, limit=None)
    
    if not history_df.empty:
        latest_history_run = history_df.iloc[0]
        st.write(f"### **Most Recent Run Insight** ({latest_history_run['date']})")
        
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Distance", f"{latest_history_run['distance']} km")
        c2.metric("Duration", format_duration(latest_history_run['duration']))
        c3.metric("Pace", format_pace(latest_history_run['pace']) + " /km")
        c4.metric("Avg HR", f"{latest_history_run['avg_hr']} bpm")
        
        insight_text = latest_history_run.get('insight', '')
        if not insight_text or str(insight_text).strip() in ['', 'None', 'No detailed insight generated.']:
            insight_text = "🔄 *Click 'Refresh Plan' in the 'My Training Plan' tab to generate a detailed qualitative analysis for your latest run.*"
            
        st.markdown(f'<div class="insight-box"><strong>🧠 Qualitative Analysis:</strong><br><br>{insight_text}</div>', unsafe_allow_html=True)
        st.divider()

        st.subheader("📋 All Past Runs")
        display_history_df = history_df.copy()
        
        display_history_df['Distance (km)'] = display_history_df['distance'].apply(lambda x: f"{float(x):.2f}")
        display_history_df['Duration (h/min)'] = display_history_df['duration'].apply(format_duration)
        display_history_df['Pace (min/sec)'] = display_history_df['pace'].apply(format_pace)
        display_history_df['Avg HR'] = display_history_df['avg_hr'].apply(lambda x: f"{float(x):.1f}" if pd.notnull(x) and x > 0 else "N/A")
        
        display_history_df = display_history_df.rename(columns={'date': 'Date', 'run_type': 'Type'})
        display_cols = ['Date', 'Type', 'Distance (km)', 'Pace (min/sec)', 'Duration (h/min)', 'Avg HR']
        st.dataframe(display_history_df[display_cols], width="stretch", hide_index=True)
        
        st.divider()
        
        st.subheader("📊 Historical Mileage")
        period = st.selectbox("Group By:", ["Day", "Week", "Month", "Year"])
        
        df_chart = history_df.copy()
        df_chart['date_dt'] = pd.to_datetime(df_chart['date'])
        df_chart = df_chart.set_index('date_dt')
        
        if period == "Day":
            grouped = df_chart.resample('D')['distance'].sum().reset_index()
            date_format = "%Y-%m-%d"
        elif period == "Week":
            grouped = df_chart.resample('W-MON')['distance'].sum().reset_index()
            date_format = "%Y-%m-%d"
        elif period == "Month":
            grouped = df_chart.resample('MS')['distance'].sum().reset_index()
            date_format = "%Y-%m"
        else:
            grouped = df_chart.resample('YS')['distance'].sum().reset_index()
            date_format = "%Y"
            
        grouped['date_str'] = grouped['date_dt'].dt.strftime(date_format)
        st.line_chart(grouped.set_index('date_str')['distance'])
        
        st.divider()
        
        st.subheader("📈 Pace Trend (Lower is faster)")
        pace_df = history_df[['date', 'pace', 'run_type']].copy()
        pace_df['date'] = pd.to_datetime(pace_df['date'])
        pace_df['formatted_pace'] = pace_df['pace'].apply(format_pace)
        
        pace_chart = alt.Chart(pace_df).mark_line(point=True).encode(
            x=alt.X('date:T', title='Date'),
            y=alt.Y('pace:Q', scale=alt.Scale(zero=False), title='Pace (min/km)'),
            color=alt.Color('run_type:N', title='Run Type'),
            tooltip=[
                alt.Tooltip('date:T', title='Date', format='%Y-%m-%d %H:%M'), 
                alt.Tooltip('run_type:N', title='Type'),
                alt.Tooltip('formatted_pace:N', title='Pace (min/sec)')
            ]
        ).properties(height=350)
        st.altair_chart(pace_chart, width="stretch")
        
    else:
        st.info("No runs logged yet. Upload a TCX file to start building your history.")

with tab5:
    st.header("⚙️ Manage History")
    
    history_df = get_run_history(USER_ID, limit=None)
    
    if not history_df.empty:
        st.write("Edit the **Run Type** directly in the table below, or select rows and press **Delete** on your keyboard to remove them. Click **Save Changes** when you are done.")
        
        edit_df = history_df[['id', 'date', 'distance', 'run_type', 'pace', 'avg_hr']].copy()
        
        edited_df = st.data_editor(
            edit_df,
            column_config={
                "id": None, 
                "date": st.column_config.TextColumn("Date", disabled=True),
                "distance": st.column_config.NumberColumn("Distance (km)", disabled=True),
                "run_type": st.column_config.SelectboxColumn(
                    "Run Type",
                    options=["Easy", "Interval", "Tempo", "Long Run"],
                    required=True
                ),
                "pace": st.column_config.NumberColumn("Pace (/km)", disabled=True),
                "avg_hr": st.column_config.NumberColumn("Avg HR", disabled=True),
            },
            disabled=["date", "distance", "pace", "avg_hr"],
            hide_index=True,
            num_rows="dynamic",
            key="history_editor",
            width="stretch"
        )
        
        if st.button("💾 Save Changes", type="primary"):
            original_ids = set(edit_df['id'])
            current_ids = set(edited_df['id'])
            deleted_ids = original_ids - current_ids
            
            for d_id in deleted_ids:
                delete_run(USER_ID, d_id)
                
            for index, row in edited_df.iterrows():
                orig_row = edit_df[edit_df['id'] == row['id']].iloc[0]
                if orig_row['run_type'] != row['run_type']:
                    update_run_type(USER_ID, row['id'], row['run_type'])
                    
            st.success("Changes saved successfully!")
            st.rerun()
            
        st.divider()
        
        st.subheader("📸 Update Historical Insights")
        st.write("Upload screenshots of lap paces/HR graphs to update the AI analysis for any previous run.")
        
        col_hist1, col_hist2 = st.columns([2, 1])
        with col_hist1:
            run_options = history_df.to_dict('records')
            selected_run_to_enrich = st.selectbox(
                "Select Historical Run", 
                options=run_options, 
                format_func=lambda x: f"{x['date']} — {x['run_type']} ({x['distance']} km)",
                key="enrich_select",
                label_visibility="collapsed"
            )
            hist_screenshot_files = st.file_uploader("Upload Screenshots", type=['png', 'jpg', 'jpeg'], accept_multiple_files=True, key="hist_upload", label_visibility="collapsed")
            
        with col_hist2:
            if st.button("Generate Insight", type="primary", width="stretch"):
                if not API_KEY:
                    st.error("API Key is missing!")
                elif not hist_screenshot_files:
                    st.warning("Please upload at least one screenshot.")
                else:
                    with st.spinner("Analyzing screenshots..."):
                        pseudo_metrics = {
                            'distance': selected_run_to_enrich['distance'],
                            'duration': selected_run_to_enrich['duration'],
                            'avg_hr': selected_run_to_enrich['avg_hr'],
                            'formatted_pace': format_pace(selected_run_to_enrich['pace'])
                        }
                        detailed_context = generate_detailed_context(pseudo_metrics, pd.DataFrame(), selected_run_to_enrich['run_type'])
                        
                        try:
                            new_insight = generate_historical_insight(detailed_context, hist_screenshot_files, selected_run_to_enrich['date'])
                            update_run_insight(USER_ID, selected_run_to_enrich['id'], new_insight)
                            st.success("Insight successfully updated!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error communicating with AI: {e}")
            
    else:
        st.info("No runs available to manage.")

# --- ADMIN DB MANAGEMENT TAB ---
if is_admin:
    with tab_admin:
        st.header("🛠️ Database Administration")
        st.warning("⚠️ **Warning:** You have direct backend access. Manually editing or deleting records here can break the app for users.")
        
        # 1. Download Database
        if os.path.exists("coach.db"):
            with open("coach.db", "rb") as f:
                st.download_button(
                    label="💾 Download SQLite Database (coach.db)", 
                    data=f, 
                    file_name="coach.db",
                    mime="application/octet-stream"
                )
        
        st.divider()
        
        # 2. Table Viewer
        st.subheader("🔍 View Tables")
        with sqlite3.connect('coach.db') as conn:
            tables = pd.read_sql_query("SELECT name FROM sqlite_master WHERE type='table';", conn)['name'].tolist()
            selected_table = st.selectbox("Select a table to view:", tables)
            
            if selected_table:
                df_table = pd.read_sql_query(f"SELECT * FROM {selected_table}", conn)
                st.dataframe(df_table, width="stretch")
        
        st.divider()
        
        # 3. Raw SQL Console
        st.subheader("⚡ Execute Raw SQL")
        sql_query = st.text_area("SQL Query (e.g., SELECT * FROM users, or DELETE FROM runs WHERE id = 5)", height=100)
        
        if st.button("Run Query", type="primary"):
            if not sql_query.strip():
                st.warning("Please enter a SQL query.")
            else:
                try:
                    with sqlite3.connect('coach.db') as conn:
                        # Handle SELECT queries by displaying the results
                        if sql_query.strip().upper().startswith("SELECT"):
                            res_df = pd.read_sql_query(sql_query, conn)
                            st.dataframe(res_df, width="stretch")
                        # Handle modification queries (INSERT, UPDATE, DELETE, DROP, etc.)
                        else:
                            c = conn.cursor()
                            c.execute(sql_query)
                            conn.commit()
                            st.success(f"Query executed successfully! Rows affected: {c.rowcount}")
                except Exception as e:
                    st.error(f"SQL Error: {e}")