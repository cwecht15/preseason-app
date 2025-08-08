import os
import difflib
import pandas as pd
import streamlit as st

# ---------- Config ----------
st.set_page_config(page_title="Preseason Player Co-Players Explorer", layout="wide")
DATA_DIR_DEFAULT = "data"  # change if you keep CSVs elsewhere

# ---------- Cache-busting helper ----------
def file_fingerprint(path: str) -> str:
    """Return a string that changes when the file changes (mtime/size)."""
    try:
        stat = os.stat(path)
        return f"{stat.st_mtime_ns}-{stat.st_size}"
    except FileNotFoundError:
        return "missing"

# ---------- Caching loaders ----------
@st.cache_data(show_spinner=False)
def load_csv(path, fingerprint=None):
    # 'fingerprint' is unused inside; it's only to bust the cache when the file changes
    return pd.read_csv(path)

@st.cache_data(show_spinner=True)
def load_all(data_dir):
    plays_path = os.path.join(data_dir, "plays_unique.csv")
    pp_path    = os.path.join(data_dir, "play_players.csv")
    idx_path   = os.path.join(data_dir, "players_index.csv")  # optional, used for metrics

    plays = load_csv(plays_path, fingerprint=file_fingerprint(plays_path))
    pp    = load_csv(pp_path,    fingerprint=file_fingerprint(pp_path))
    idx   = load_csv(idx_path,   fingerprint=file_fingerprint(idx_path))

    # normalize column names just in case
    for df in (plays, pp, idx):
        df.columns = [c.strip() for c in df.columns]
    return plays, pp, idx

# Manual refresh
if st.sidebar.button("🔄 Refresh data (clear cache)"):
    st.cache_data.clear()
    st.rerun()

# ---------- Helpers ----------
def normalize(s):
    return (s or "").strip()

def get_player_suggestions(query, names, n=12):
    query = normalize(query)
    if not query:
        return []
    # prefix matches first
    prefix = [nm for nm in names if nm.lower().startswith(query.lower())]
    if len(prefix) >= n:
        return sorted(prefix)[:n]
    # fuzzy fallback
    fuzzy = difflib.get_close_matches(query, names, n=n, cutoff=0.6)
    seen, out = set(), []
    for lst in (prefix, fuzzy):
        for x in lst:
            if x not in seen:
                seen.add(x)
                out.append(x)
    return out[:n]

def downloadable_csv(df, label, filename):
    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button(label, csv, file_name=filename, mime="text/csv")

def pr_filter(df):
    typ = df["nflPlayType"].fillna("").astype(str)
    return df[(typ == "RUSH") | (typ.str.startswith("PASS"))]

def get_week_options(plays_df):
    return sorted(w for w in plays_df["week"].dropna().unique().tolist())

def filter_plays_by_weeks(plays_df, weeks_selected):
    if not weeks_selected:
        return plays_df.iloc[0:0]
    return plays_df[plays_df["week"].isin(weeks_selected)].copy()

def get_player_plays(plays_df, pp_df, player_name, weeks_selected=None):
    # limit to weeks + PASS/RUSH
    plays_scoped = filter_plays_by_weeks(plays_df, weeks_selected) if weeks_selected else plays_df
    plays_scoped = pr_filter(plays_scoped)
    # plays the player appears in (any side), join to play metadata
    target = pp_df[pp_df["playerName"].str.lower() == player_name.lower()][["gameId","nflPlayId"]].drop_duplicates()
    merged = target.merge(plays_scoped, on=["gameId","nflPlayId"], how="inner")
    merged = merged.sort_values(["gameId","week","nflPlayId"], na_position="last")
    return merged

def coplayer_counts_for_weeks(pp_df, plays_df, player_name, weeks_selected=None):
    # recompute same-team co-player counts for selected weeks only
    plays_scoped = filter_plays_by_weeks(plays_df, weeks_selected) if weeks_selected else plays_df
    plays_scoped = pr_filter(plays_scoped)[["gameId","nflPlayId","week"]]

    pp_scoped = pp_df.merge(plays_scoped, on=["gameId","nflPlayId"], how="inner")

    me_rows = pp_scoped[pp_scoped["playerName"].str.lower() == player_name.lower()][["gameId","nflPlayId","teamId"]]
    me_rows = me_rows.rename(columns={"teamId":"playerTeamId"})

    ann = pp_scoped.merge(me_rows, on=["gameId","nflPlayId"], how="inner")

    same_team = ann[
        (ann["teamId"] == ann["playerTeamId"]) &
        (ann["playerName"].str.lower() != player_name.lower())
    ]

    out = (same_team.groupby(["playerName","teamId"], as_index=False)
           .size()
           .rename(columns={"playerName":"teammate","size":"count"}))
    out = out.sort_values(["count","teammate"], ascending=[False, True])
    return out

def pass_rush_snaps_for_weeks(pp_df, plays_df, player_name, weeks_selected=None):
    plays_scoped = filter_plays_by_weeks(plays_df, weeks_selected) if weeks_selected else plays_df
    plays_scoped = pr_filter(plays_scoped)[["gameId","nflPlayId"]]
    pp_scoped = pp_df[pp_df["playerName"].str.lower() == player_name.lower()][["gameId","nflPlayId","teamId","position"]]
    merged = pp_scoped.merge(plays_scoped, on=["gameId","nflPlayId"], how="inner").drop_duplicates()
    snaps = int(len(merged))
    teams = ", ".join(sorted({str(t) for t in merged["teamId"].dropna().unique().tolist()})) if not merged.empty else ""
    poss  = ", ".join(sorted({(str(p).strip() or "Unknown") for p in merged["position"].dropna().unique().tolist()})) if not merged.empty else ""
    return snaps, teams, poss

def get_teammates_on_play(pp_df, play_row, player_name):
    mask = (pp_df["gameId"] == play_row["gameId"]) & (pp_df["nflPlayId"] == play_row["nflPlayId"])
    snap = pp_df.loc[mask, ["playerName","teamId","position"]].copy()
    if snap.empty:
        return []
    team_id = snap.loc[snap["playerName"].str.lower() == player_name.lower(), "teamId"]
    if team_id.empty:
        return []
    tid = team_id.iloc[0]
    mates = snap[(snap["teamId"] == tid) & (snap["playerName"].str.lower() != player_name.lower())]
    return [f"{r.playerName} ({normalize(r.position) or 'Unknown'})" for _, r in mates.iterrows()]

# ---------- Sidebar: data source ----------
st.sidebar.header("Data Source")
mode = st.sidebar.radio("Load data from…", ["Folder", "Manual upload"], index=0)

if mode == "Folder":
    data_dir = st.sidebar.text_input("Folder containing the CSVs", value=DATA_DIR_DEFAULT)
    if not os.path.isdir(data_dir):
        st.sidebar.warning("Folder not found. Create it and place the CSVs inside.")
        st.stop()
    try:
        plays_df, pp_df, idx_df = load_all(data_dir)
    except Exception as e:
        st.sidebar.error(f"Failed to load CSVs from {data_dir}\n{e}")
        st.stop()
else:
    plays_file = st.sidebar.file_uploader("Upload plays_unique.csv", type=["csv"])
    pp_file    = st.sidebar.file_uploader("Upload play_players.csv", type=["csv"])
    idx_file   = st.sidebar.file_uploader("Upload players_index.csv", type=["csv"])
    if not all([plays_file, pp_file, idx_file]):
        st.info("Upload all three CSVs to proceed.")
        st.stop()
    plays_df = load_csv(plays_file)
    pp_df    = load_csv(pp_file)
    idx_df   = load_csv(idx_file)

# ---------- Sidebar: Filters ----------
st.sidebar.header("Filters")
all_weeks = get_week_options(plays_df)
if not all_weeks:
    weeks_selected = []
    st.sidebar.caption("No week values found.")
else:
    weeks_selected = st.sidebar.multiselect(
        "Weeks",
        options=all_weeks,
        default=all_weeks,
        help="Select one or more weeks to filter results."
    )

# ---------- Main UI ----------
st.title("Preseason Player Co-Players Explorer")

# Player input + suggestions
# Use ALL players from play_players.csv so newly-added players show up immediately
all_names = sorted(pp_df["playerName"].dropna().unique().tolist())

col_q, col_s = st.columns([2, 1])
with col_q:
    query = st.text_input("Search player", placeholder="e.g., Isaac TeSlaa")
with col_s:
    suggestions = get_player_suggestions(query, all_names) if query else []
    pick = st.selectbox("Suggestions", options=["(none)"] + suggestions, index=0)

player_name = None
if query and pick == "(none)":
    if query in all_names:
        player_name = query
    elif len(suggestions) == 1:
        player_name = suggestions[0]
    else:
        st.info("Pick a suggestion or type the exact player name.")
elif pick and pick != "(none)":
    player_name = pick

if not player_name:
    st.caption("Tip: Start typing and choose from suggestions to view results.")
    st.stop()

# ---------- Player header / summary (week-aware) ----------
st.subheader(player_name)
snaps, teams, poss = pass_rush_snaps_for_weeks(pp_df, plays_df, player_name, weeks_selected)

met1, met2, met3 = st.columns(3)
met1.metric("PASS/RUSH snaps (filtered)", snaps)
met2.metric("TeamId(s)", teams or "—")
met3.metric("Position(s)", poss or "—")

st.divider()

# ---------- Co-player counts (week-aware) ----------
st.markdown("### Same-Team Co-Players on PASS/RUSH Plays")
top_n = st.slider("Top N", 5, 50, 20, help="Number of teammates to show")

cop = coplayer_counts_for_weeks(pp_df, plays_df, player_name, weeks_selected)
if cop.empty:
    st.info("No co-player data for this player with current week filter.")
else:
    cop_disp = cop.copy()
    cop_disp["Teammate"] = cop_disp["teammate"].astype(str)
    cop_disp["TeamId"]   = cop_disp["teamId"].astype("Int64")
    cop_disp = cop_disp[["Teammate","TeamId","count"]].rename(columns={"count":"Plays together"})
    st.dataframe(cop_disp.head(top_n), use_container_width=True, height=400)
    downloadable_csv(cop_disp, "Download co-player counts (CSV)", f"{player_name}_coplayers_wk.csv")

    chart_src = cop_disp.rename(columns={"Teammate":"label","Plays together":"value"}).head(top_n)
    if not chart_src.empty:
        st.bar_chart(chart_src.set_index("label")["value"])

st.divider()

# ---------- Plays list (week-aware) ----------
st.markdown("### Plays Involving Player (PASS/RUSH)")
plays_involving = get_player_plays(plays_df, pp_df, player_name, weeks_selected)

if plays_involving.empty:
    st.info("No matching PASS/RUSH plays found for this player with current week filter.")
else:
    with st.expander("Add ‘Other teammates on field’ column (slower)"):
        add_mates = st.checkbox("Compute teammates on each play")
        if add_mates:
            plays_involving = plays_involving.copy()
            plays_involving["OtherTeammates"] = plays_involving.apply(
                lambda r: ", ".join(get_teammates_on_play(pp_df, r, player_name)),
                axis=1
            )

    show_cols = ["gameId","week","nflPlayId","nflPlayType","nflPlayDescription","nflPlayUrl"]
    show_cols = [c for c in show_cols if c in plays_involving.columns]
    st.dataframe(plays_involving[show_cols], use_container_width=True, height=500)
    downloadable_csv(plays_involving[show_cols], "Download plays (CSV)", f"{player_name}_plays_wk.csv")

# ---------- Footer ----------
st.caption("Data source: precomputed CSVs from your preprocessing script. PASS includes any type starting with 'PASS'.")
