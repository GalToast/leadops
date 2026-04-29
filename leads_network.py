"""
Lead Network Visualization - Ultimate Edition
Run: streamlit run leads_network.py
"""

import streamlit as st
import streamlit.components.v1 as components
import sqlite3
import pandas as pd
import numpy as np
import struct
import time
import requests
import subprocess
import html
from pyvis.network import Network
from pathlib import Path
import plotly.express as px
import plotly.graph_objects as go
from core_utils import DB_PATH, get_connection, blob_to_vector

def escape_html(text):
    """Escape special characters to prevent XSS in HTML contexts."""
    if text is None:
        return ""
    return html.escape(str(text))

# Config
SERVER_PORT = 8092
LLAMA_SERVER_EXE = Path(__file__).parent / "ai-models" / "runtime" / "llama-cpp-cuda-b8508" / "llama-server.exe"
MODELS = {
    "0.6B": {
        "label": "0.6B (Fast)",
        "path": Path(__file__).parent / "ai-models" / "music" / "ACE-Step-1.5" / "checkpoints" / "Qwen3-Embedding-0.6B-GGUF" / "Qwen3-Embedding-0.6B-Q8_0.gguf",
        "cache": Path(__file__).parent / "galaxy_cache_0.6B.parquet",
        "db_key": "%0.6B%",
        "dim": 1024,
    },
    "4B": {
        "label": "4B (Precise)",
        "path": Path(__file__).parent / "ai-models" / "music" / "ACE-Step-1.5" / "checkpoints" / "Qwen3-Embedding-4B-GGUF" / "Qwen3-Embedding-4B-Q4_K_M.gguf",
        "cache": Path(__file__).parent / "galaxy_cache_4B.parquet",
        "db_key": "%4B%",
        "dim": 2560,
    }
}

conn = get_connection()

# ─────────────────────────────────────────────────────────────────────────────
# Search Engine Management
# ─────────────────────────────────────────────────────────────────────────────

def is_server_alive():
    try:
        response = requests.get(f"http://127.0.0.1:{SERVER_PORT}/health", timeout=1)
        return response.status_code == 200
    except (requests.RequestException, ConnectionError, TimeoutError):
        return False

def get_running_model_info():
    try:
        payload = {"input": ["test"]}
        response = requests.post(f"http://127.0.0.1:{SERVER_PORT}/v1/embeddings", json=payload, timeout=2)
        if response.status_code == 200:
            vec = response.json()["data"][0]["embedding"]
            return {"dim": len(vec)}
    except (requests.RequestException, ConnectionError, TimeoutError, KeyError, ValueError):
        pass
    return None

def start_search_engine(model_key):
    if not LLAMA_SERVER_EXE.exists():
        st.error(f"❌ Search engine executable not found at `{LLAMA_SERVER_EXE}`")
        return
    
    model_path = MODELS[model_key]["path"]
    if not model_path.exists():
        st.error(f"❌ Model file not found at `{model_path}`")
        return

    subprocess.run(["powershell", "-Command", "Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force"], capture_output=True)
    time.sleep(2)
    cmd = f'Start-Process -WindowStyle Hidden -FilePath "{LLAMA_SERVER_EXE}" -ArgumentList "--model", """{model_path}""", "--embeddings", "--pooling", "mean", "--port", "{SERVER_PORT}", "--host", "127.0.0.1", "--device", "CUDA0", "--gpu-layers", "auto", "--ctx-size", "4096", "--no-webui"'
    subprocess.Popen(["powershell", "-Command", cmd])

def get_query_embedding(query_text):
    if not is_server_alive(): return None
    payload = {"input": [query_text]}
    try:
        response = requests.post(f"http://127.0.0.1:{SERVER_PORT}/v1/embeddings", json=payload, timeout=60)
        if response.status_code == 200:
            return np.array(response.json()["data"][0]["embedding"], dtype=np.float32)
    except Exception as e:
        st.error(f"Search Engine busy: {e}")
    return None

# ─────────────────────────────────────────────────────────────────────────────
# Data Loading
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data
def load_baked_galaxy(model_key):
    cache_path = MODELS[model_key]["cache"]
    if not cache_path.exists(): return None
    # Ensure map is loaded in a STRICT sorted order to match the math vectors later
    return pd.read_parquet(cache_path).sort_values('lead_id', ascending=True).reset_index(drop=True)

@st.cache_data(ttl=300)
def load_heavy_vectors_for_search(model_key):
    cache_path = Path(__file__).parent / ".cache" / "embeddings" / f"vectors_{model_key}.npz"
    if cache_path.exists():
        data = np.load(cache_path)
        return data['ids'].tolist(), data['matrix']
    
    # Fallback to DB if cache missing
    db_pattern = MODELS[model_key]["db_key"]
    model_key_row = pd.read_sql_query("SELECT embedding_model FROM leadops_vector_embeddings WHERE embedding_model LIKE ? LIMIT 1", conn, params=[db_pattern])
    if model_key_row.empty: return None, None
    actual_model_name = model_key_row.iloc[0]['embedding_model']
    
    # CRITICAL: STRICT ORDERING TO MATCH THE BAKED GALAXY
    df = pd.read_sql_query("""
        SELECT lead_id, vector_blob, embedding_dim
        FROM leadops_vector_embeddings 
        WHERE doc_type = 'profile_markdown' AND embedding_model = ?
        ORDER BY lead_id ASC
    """, conn, params=[actual_model_name])
    
    if df.empty: return None, None
    dim = df.iloc[0]['embedding_dim']
    vectors = [blob_to_vector(blob, dim) for blob in df['vector_blob']]
    return df['lead_id'].tolist(), np.vstack(vectors)

# ─────────────────────────────────────────────────────────────────────────────
# Visualization
# ─────────────────────────────────────────────────────────────────────────────

def create_galaxy_plot(df, focus_ids=None, model_label="0.6B", model_dim=1024):
    """Create the semantic galaxy scatter plot with UMAP coordinates.
    
    Args:
        df: DataFrame with lead data including umap_x, umap_y columns
        focus_ids: List of lead IDs to highlight (from search/LASSO)
        model_label: Human-readable model name for the title
        model_dim: Embedding dimension for display
    """
    if df is None or len(df) == 0: return None

    family_colors = {
        'commercial': '#4ECDC4', 'enterprise': '#FF6B6B', 'mission_style': '#95E1D3',
        'care_practice': '#C9B1FF', 'public_institution': '#FFD93D', 'education': '#4D96FF',
        'unknown': '#6C757D'
    }
    df['color_family'] = df['audience_family'].apply(lambda x: x if x in family_colors else 'unknown')

    total_leads = len(df)
    fig = px.scatter(
        df, x='umap_x', y='umap_y',
        color='color_family', color_discrete_map=family_colors,
        hover_name='name',
        hover_data={'umap_x': False, 'umap_y': False, 'audience_family': True, 'audience_type': True, 'status': True, 'lead_id': True},
        title=f'Semantic Galaxy • {model_label} ({model_dim}D) • {total_leads:,} leads', 
        template='plotly_dark', render_mode='webgl'
    )
    
    # Update legend title to be more descriptive
    fig.update_layout(legend_title_text='Audience Family')
    
    fig.update_traces(marker=dict(size=5, opacity=0.8, line=dict(width=0)))
    
    if focus_ids is not None and len(focus_ids) > 0:
        # Dim background points
        fig.update_traces(marker=dict(opacity=0.08), hoverinfo='skip', hovertemplate=None, selector=lambda t: t.name != 'Search Results')
        focus_df = df[df['lead_id'].isin(focus_ids)]
        hover_text = focus_df.apply(lambda r: f"<b>{r['name']}</b><br>ID: {r['lead_id']}<br>Family: {r['audience_family']}<br>Type: {r['audience_type']}<br>Status: {r['status']}", axis=1)

        # Add highlighted points with labels (truncate with ellipsis)
        fig.add_trace(go.Scattergl(
            x=focus_df['umap_x'], y=focus_df['umap_y'],
            mode='markers+text',
            text=focus_df['name'].apply(lambda x: x[:16] + ('…' if len(str(x)) > 16 else '')),
            textposition="top center",
            textfont=dict(color='white', size=12, family="system-ui"),
            marker=dict(size=14, color='#FFD93D', opacity=1.0, line=dict(width=2, color='white')),
            name='Search Results', hovertext=hover_text, hoverinfo='text'
        ))
        
        # Auto-zoom to focused area
        x_min, x_max = focus_df['umap_x'].min(), focus_df['umap_x'].max()
        y_min, y_max = focus_df['umap_y'].min(), focus_df['umap_y'].max()
        x_pad, y_pad = max((x_max - x_min) * 0.5, 0.5), max((y_max - y_min) * 0.5, 0.5)
        fig.update_layout(xaxis=dict(range=[x_min - x_pad, x_max + x_pad], visible=False), yaxis=dict(range=[y_min - y_pad, y_max + y_pad], visible=False))
    else:
        fig.update_layout(xaxis=dict(visible=False), yaxis=dict(visible=False))

    fig.update_layout(
        plot_bgcolor='#0D1117', 
        paper_bgcolor='#0D1117', 
        font_color='#E6EDF3', 
        margin=dict(l=0, r=0, t=40, b=0), 
        dragmode='lasso',
        # Add annotation for LASSO instructions
        annotations=[dict(
            text="💡 Use LASSO (lasso icon) to select groups • Double-click to reset",
            xref="paper", yref="paper",
            x=0.5, y=-0.02, showarrow=False,
            font=dict(size=11, color='#8B949E')
        )] if focus_ids is None or len(focus_ids) == 0 else []
    )
    return fig

# ─────────────────────────────────────────────────────────────────────────────
# MAIN EXECUTION
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_resource
def render_spider_web_html(focus_ids, edges_tuple, df_subset_json, threshold):
    """Generate and cache the Spider Web HTML to prevent expensive regeneration."""
    df_subset = pd.read_json(df_subset_json)
    
    net = Network(height='600px', width='100%', bgcolor='#0D1117', font_color='#E6EDF3', directed=False)
    
    # Configure physics for better node separation and stable layout
    net.barnes_hut(
        gravity=-3000,
        central_gravity=0.3,
        spring_length=150,
        spring_strength=0.05,
        damping=0.85
    )
    
    # Stabilization: Disable physics after it settles to save CPU
    net.set_options("""
    {
        "physics": {
            "stabilization": {
                "enabled": true,
                "iterations": 200,
                "updateInterval": 50
            }
        }
    }
    """)
    
    status_colors = {
        'ready': '#4CAF50',      # Green
        'complete': '#2196F3',   # Blue
        'draft-prepared': '#FF9800',  # Orange
        'disqualified': '#F44336',  # Red
        'new': '#9E9E9E',        # Grey
        'researching': '#9C27B0'  # Purple
    }
    
    for _, row in df_subset.iterrows():
        lead_id = row['lead_id']
        lead_name = row['name'][:25] if len(str(row['name'])) > 25 else row['name']
        lead_status = str(row.get('status', 'unknown')).lower()
        lead_name_safe = escape_html(lead_name)
        node_color = status_colors.get(lead_status, '#FFD93D')
        title_html = f'<a href="/?lead_id={int(lead_id)}" target="_blank" style="color:#58a6ff;font-weight:bold;">View Details →</a><br><b>{lead_name_safe}</b><br>Status: {lead_status}'
        net.add_node(
            int(lead_id), 
            label=lead_name_safe, 
            title=title_html,
            color=node_color, 
            size=20
        )
    
    for edge in edges_tuple:
        source, target, similarity = edge
        edge_width = 1 + (similarity - 0.8) * 15
        edge_color = f'rgba(120, 180, 255, {0.3 + (similarity - 0.8) * 2})'
        net.add_edge(source, target, width=edge_width, color=edge_color, title=f"{similarity:.0%} similar")
    
    html_content = net.generate_html()
    
    click_script = """
<script>
(function() {
    let attempts = 0;
    const maxAttempts = 50;
    function attachClickHandler() {
        const networkKeys = Object.keys(window).filter(k => 
            k.startsWith('network') && window[k] && typeof window[k].on === 'function'
        );
        if (networkKeys.length === 0) {
            attempts++;
            if (attempts < maxAttempts) setTimeout(attachClickHandler, 100);
            return;
        }
        const network = window[networkKeys[0]];
        network.on('click', function(params) {
            if (params.nodes && params.nodes.length > 0) {
                const nodeId = params.nodes[0];
                window.open('/?lead_id=' + nodeId, '_blank');
            }
        });
    }
    if (document.readyState === 'complete') attachClickHandler();
    else window.addEventListener('load', attachClickHandler);
})();
</script>
"""
    return html_content.replace('</body>', click_script + '</body>')

def main():
    # Page config is now in pages/1_Network_View.py for multipage support

    # Header with back link
    col_title, col_back = st.columns([4, 1])
    with col_title:
        st.title("🌌 Lead Galaxy")
        st.caption("Explore semantic relationships between leads • Use LASSO on map or search to select")
    with col_back:
        st.page_link("app.py", label="🔍 Back to Dashboard")

    # Model selection with explanation
    st.sidebar.markdown("### 🌐 Universe")
    selected_model = st.sidebar.radio(
        "Select Universe", 
        options=list(MODELS.keys()), 
        format_func=lambda x: MODELS[x]["label"],
        help="0.6B (Fast): Quick searches, good accuracy\n4B (Precise): Slower but more accurate semantic matching"
    )

    df_galaxy = load_baked_galaxy(selected_model)
    
    # Map Coverage Check
    if df_galaxy is not None:
        try:
            db_count = pd.read_sql_query("SELECT COUNT(*) as cnt FROM leadops_leads", conn).iloc[0]['cnt']
            map_count = len(df_galaxy)
            coverage = (map_count / db_count) * 100 if db_count > 0 else 100
            
            st.sidebar.markdown(f"**Map Coverage:** {coverage:.1f}%")
            if coverage < 95:
                st.sidebar.warning(f"⚠️ {db_count - map_count} leads are missing from this map. Run `bake_galaxy.py` to update.")
            else:
                st.sidebar.caption(f"Showing {map_count:,} of {db_count:,} leads")
        except Exception:
            pass

    # Engine Management
    server_ready = is_server_alive()
    engine_info = get_running_model_info() if server_ready else None
    expected_dim = 1024 if selected_model == "0.6B" else 2560
    current_dim = engine_info["dim"] if engine_info else 0
    should_auto_swap = server_ready and (current_dim != expected_dim)

    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🧠 Search Engine")

    # Prevent swap loop
    if 'last_swap' not in st.session_state: st.session_state.last_swap = ""

    if not server_ready or should_auto_swap:
        st.sidebar.warning("🔴 Offline" if not server_ready else "⚠️ Dimension Mismatch")
        if st.sidebar.button("🚀 Start Engine", type="primary") or (should_auto_swap and st.session_state.last_swap != selected_model):
            st.session_state.last_swap = selected_model
            with st.status(f"Starting {selected_model} Engine...", expanded=True) as status:
                start_search_engine(selected_model)
                for i in range(15):
                    time.sleep(2)
                    if is_server_alive():
                        info = get_running_model_info()
                        if info and info['dim'] == expected_dim:
                            status.update(label="Ready!", state="complete")
                            break
                st.rerun()
    else:
        st.sidebar.success(f"🟢 Online ({selected_model})")

    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🔍 Find Leads")
    
    # Initialize search state BEFORE widgets
    if 'network_semantic_query' not in st.session_state:
        st.session_state.network_semantic_query = ""
    if 'network_highlight_name' not in st.session_state:
        st.session_state.network_highlight_name = ""
    if 'clear_network_search' not in st.session_state:
        st.session_state.clear_network_search = False
    
    # Handle clear button BEFORE widgets are created
    if st.session_state.clear_network_search:
        st.session_state.network_semantic_query = ""
        st.session_state.network_highlight_name = ""
        st.session_state.clear_network_search = False
    
    semantic_query = st.sidebar.text_input("Concept Search (AI)", placeholder="e.g. 'Ranchers in Texas'", help="Search by meaning, not just keywords", key="network_semantic_query")
    highlight_name = st.sidebar.text_input("Exact Name", placeholder="e.g. 'Conroe Medical'", help="Find leads with names containing this text", key="network_highlight_name")
    
    # Clear search button - sets flag for next rerun
    if semantic_query or highlight_name:
        if st.sidebar.button("🗑️ Clear Search", use_container_width=True):
            st.session_state.clear_network_search = True
            st.rerun()

    with st.sidebar.expander("🛠️ Settings"):
        threshold = st.slider("Web Threshold", 0.80, 0.99, 0.92, step=0.01, help="Minimum similarity for edge connections. Higher = fewer, stronger connections.")
        st.caption(f"Only show connections ≥ {threshold:.0%} similar")
        if st.button("🧹 Reset UI"):
            st.cache_data.clear()
            st.query_params.clear()
            st.rerun()

    if df_galaxy is None:
        st.error(f"Galaxy cache for {selected_model} not found! Run bake_galaxy.py.")
    else:
        focus_ids = []
        edges = []
        error_msg = None

        if semantic_query or highlight_name:
            with st.spinner(f"Searching..."):
                lead_ids_list, vectors_arr = load_heavy_vectors_for_search(selected_model)
                if lead_ids_list is not None:
                    if semantic_query:
                        if not server_ready or (current_dim != expected_dim):
                            error_msg = "Match engine mismatch. Restarting..."
                        else:
                            query_vec = get_query_embedding(semantic_query)
                            if query_vec is not None:
                                try:
                                    # Normalize with zero-vector protection
                                    norms = np.linalg.norm(vectors_arr, axis=1)
                                    norms = np.clip(norms, 1e-10, None)  # Prevent division by zero
                                    query_norm = np.linalg.norm(query_vec)
                                    if query_norm < 1e-10:
                                        error_msg = "Query vector is zero. Cannot compute similarity."
                                    else:
                                        similarities = np.dot(vectors_arr, query_vec) / (norms * query_norm)
                                        df_galaxy['match_score'] = (similarities * 100).round(1)
                                        top_indices = np.argsort(similarities)[-40:][::-1]
                                        focus_ids = [lead_ids_list[i] for i in top_indices]
                                except (ValueError, IndexError, ZeroDivisionError) as calc_err:
                                    error_msg = f"Calculation error: {calc_err}"
                    elif highlight_name:
                        matches = df_galaxy[df_galaxy['name'].str.contains(highlight_name, case=False, na=False)]
                        if not matches.empty:
                            target_id = matches.iloc[0]['lead_id']
                            if target_id in lead_ids_list:
                                target_idx = lead_ids_list.index(target_id)
                                target_vec = vectors_arr[target_idx]
                                # Normalize with zero-vector protection
                                norms = np.linalg.norm(vectors_arr, axis=1)
                                norms = np.clip(norms, 1e-10, None)
                                target_norm = np.linalg.norm(target_vec)
                                if target_norm >= 1e-10:
                                    similarities = np.dot(vectors_arr, target_vec) / (norms * target_norm)
                                    df_galaxy['match_score'] = (similarities * 100).round(1)
                                    top_indices = np.argsort(similarities)[-30:][::-1]
                                    focus_ids = [lead_ids_list[i] for i in top_indices]
                                else:
                                    error_msg = "Target vector is zero. Cannot compute similarity."
                    
                    # SHARED EDGE CALC
                    if focus_ids and vectors_arr is not None:
                        f_indices = [lead_ids_list.index(fid) for fid in focus_ids]
                        l_vecs = vectors_arr[f_indices]
                        l_norms = np.linalg.norm(l_vecs, axis=1)[:, np.newaxis]
                        l_norms = np.clip(l_norms, 1e-10, None)  # Prevent division by zero
                        l_vecs_norm = l_vecs / l_norms
                        l_matrix = np.dot(l_vecs_norm, l_vecs_norm.T)
                        for i in range(len(focus_ids)):
                            for j in range(i+1, len(focus_ids)):
                                if l_matrix[i, j] >= threshold:
                                    edges.append({'source': focus_ids[i], 'target': focus_ids[j], 'similarity': float(l_matrix[i, j])})

        if error_msg: st.warning(f"⚠️ {error_msg}")

        tab1, tab2 = st.tabs(["🌌 Map & Results", "🕸️ Spider Web"])
        
        with tab1:
            col1, col2 = st.columns([2, 1])
            with col1:
                model_dim = MODELS[selected_model]["dim"]
                fig = create_galaxy_plot(df_galaxy, focus_ids, MODELS[selected_model]["label"], model_dim)
                # Capture selection!
                selection = st.plotly_chart(fig, use_container_width=True, height=650, on_select="rerun", selection_mode=['lasso', 'box'])
                
                # If user LASSOED a group, update focus_ids!
                if selection and "selection" in selection and "point_indices" in selection["selection"]:
                    selected_indices = selection["selection"]["point_indices"]
                    if selected_indices:
                        focus_ids = df_galaxy.iloc[selected_indices]['lead_id'].tolist()
                        st.toast(f"✓ Captured {len(focus_ids)} leads from map selection")

            with col2:
                if focus_ids:
                    # Header with clear button
                    header_col, clear_col = st.columns([4, 1])
                    with header_col:
                        st.markdown(f"### 🎯 Results ({len(focus_ids)} leads)")
                    with clear_col:
                        if st.button("🗑️ Clear", key="clear_results", use_container_width=True, help="Clear selection"):
                            st.session_state.clear_network_search = True
                            st.rerun()
                    d_df = df_galaxy[df_galaxy['lead_id'].isin(focus_ids)].copy()
                    cols_to_show = ['name', 'audience_type', 'status']
                    if 'match_score' in d_df.columns: 
                        d_df = d_df.sort_values('match_score', ascending=False)
                        cols_to_show = ['match_score'] + cols_to_show
                    st.dataframe(d_df[cols_to_show], hide_index=True, height=550, use_container_width=True)
                    
                    # Quick stats
                    st.caption(f"📊 Status breakdown:")
                    status_counts = d_df['status'].value_counts()
                    status_icons = {'ready': '🟢', 'complete': '🔵', 'disqualified': '🔴', 'draft-prepared': '🟠', 'new': '⚫'}
                    status_line = " • ".join([f"{status_icons.get(s, '⚪')} {s}: {c}" for s, c in status_counts.items()])
                    st.caption(status_line)
                else:
                    st.info("Search a concept or use the LASSO tool on the map to see leads here.")
                    st.markdown("""
                    **Quick Tips:**
                    - 🖱️ **LASSO:** Draw a selection on the map
                    - 🔍 **Concept Search:** Type in the sidebar
                    - 🎯 **Exact Name:** Find by company name
                    """)

        with tab2:
            st.markdown("### 🕸️ Spider Web Visualization")
            if focus_ids:
                # Selection controls
                sel_col1, sel_col2, sel_col3 = st.columns([2, 1, 1])
                with sel_col1:
                    st.caption(f"📍 **{len(focus_ids)} leads selected**")
                with sel_col2:
                    if st.button("🗑️ Clear Selection", use_container_width=True):
                        st.session_state.clear_network_search = True
                        st.rerun()
                with sel_col3:
                    if len(focus_ids) > 50 and st.button("✂️ Keep Top 50", use_container_width=True, help="Keep only the 50 most relevant leads"):
                        st.toast(f"✓ Reduced to 50 leads")
                        st.rerun()
                
                # Warn if too many nodes for good performance
                if len(focus_ids) > 100:
                    st.warning(f"⚠️ {len(focus_ids)} nodes selected. For best performance, limit to <100 leads using LASSO or search refinement.")
                
                # Legend and stats
                legend_col1, legend_col2, legend_col3 = st.columns(3)
                with legend_col1:
                    st.caption(f"**{len(focus_ids)} nodes** (leads)")
                with legend_col2:
                    st.caption(f"**{len(edges)} edges** (connections)")
                with legend_col3:
                    st.caption(f"**Threshold:** {threshold:.0%} similarity")
                
                st.caption("💡 **Tip:** Hover over a node to see the lead name. Click to open lead details in a new tab. Edge thickness shows similarity strength.")
                
                # Color legend
                with st.expander("🎨 Node Color Legend", expanded=False):
                    st.markdown("""
                    | Color | Status | Meaning |
                    |-------|--------|---------|
                    | 🟢 Green | Ready | Ready to contact |
                    | 🔵 Blue | Complete | Already contacted |
                    | 🟠 Orange | Draft-Prepared | Draft ready |
                    | 🔴 Red | Disqualified | Do not contact |
                    | ⚫ Grey | New | Not yet processed |
                    | 🟣 Purple | Researching | Needs research |
                    """)
                
                # Use cached HTML renderer
                edges_tuple = tuple((e['source'], e['target'], e['similarity']) for e in edges)
                df_subset = df_galaxy[df_galaxy['lead_id'].isin(focus_ids)]
                df_subset_json = df_subset.to_json()
                
                html_with_click = render_spider_web_html(tuple(focus_ids), edges_tuple, df_subset_json, threshold)
                components.html(html_with_click, height=650, scrolling=False)
            else:
                st.info("📍 No active selection.")
                st.markdown("""
**How to populate the Spider Web:**
1. **LASSO Tool:** Draw a selection on the Semantic Galaxy map (Tab 1)
2. **Concept Search:** Enter a semantic query in the sidebar
3. **Exact Name:** Find a specific lead by name

The Spider Web shows **similarity connections** between leads.  
Each edge represents a semantic relationship above the threshold.
""")


