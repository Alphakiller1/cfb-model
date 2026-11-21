"""Review saved, pre-kickoff CFB forecasts and their recorded outcomes."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import ledger


def _prestart(row: dict) -> bool:
    saved, kickoff = ledger._parse(row.get("recorded_at")), ledger._parse(row.get("kickoff"))
    return bool(saved and kickoff and saved < kickoff)


def payload(history: dict, *, season: int) -> dict:
    """Select existing vintages; never calculate a replacement historical forecast."""
    latest = {}
    excluded = 0
    for row in history.get("snapshots", []):
        if row.get("season") != season:
            continue
        if not _prestart(row):
            excluded += 1
            continue
        key = (row.get("week"), row.get("home"), row.get("away"))
        if key not in latest or row["recorded_at"] > latest[key]["recorded_at"]:
            latest[key] = row
    games = [dict(row) for row in latest.values()]
    players = []
    for row in history.get("player_snapshots", []):
        if row.get("season") != season:
            continue
        if not _prestart(row):
            excluded += 1
            continue
        for stat, projected in (row.get("metrics") or {}).items():
            actual = (row.get("actual_metrics") or {}).get(stat)
            players.append({
                "week": row.get("week"), "game_key": row.get("game_key"),
                "kickoff": row.get("kickoff"), "recorded_at": row.get("recorded_at"),
                "player": row.get("player_name"), "player_id": row.get("player_id"),
                "team": row.get("team"), "opponent": row.get("opponent"),
                "position": row.get("position"), "model_version": row.get("model_version"),
                "stat": stat, "projection": projected, "actual": actual,
                "error": None if actual is None else float(projected) - float(actual),
                "status": row.get("status"), "recorded_stat_line": row.get("played"),
                "availability": row.get("availability"),
                "predictability": (row.get("predictability") or {}).get(stat),
            })
    wagers = [{**row, "selection_type": family}
              for family in ("best_bets", "sharp_spots")
              for row in history.get(family, [])
              if row.get("season") == season and _prestart(row)]
    return {
        "schema": "cfb-saved-results/1", "season": season,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": "Latest saved pre-kickoff forecast per game/player; original wager entries",
        "games": games, "players": players, "wagers": wagers,
        "excluded_invalid_vintages": excluded,
        "summary": ledger.summary(history, season=season),
    }


_JS = r"""
(() => {
  const $ = id => document.getElementById(id);
  const esc = v => String(v == null ? '—' : v).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
  const number = v => v == null || !Number.isFinite(Number(v)) ? '—' :
    Number(v).toFixed(2).replace(/\.00$/, '').replace(/(\.\d)0$/, '$1');
  const signed = v => v == null ? '—' : (Number(v)>0?'+':'')+number(v);
  const names = {pass_att:'Pass attempts',pass_cmp:'Completions',pass_yds:'Pass yards',
    pass_td:'Pass TDs',pass_int:'Interceptions',rush_car:'Carries',rush_yds:'Rush yards',
    rec:'Receptions',rec_yds:'Receiving yards'};
  const time = v => v ? new Date(v).toLocaleString('en-US', {month:'short',day:'numeric',
    hour:'numeric',minute:'2-digit',timeZone:'America/New_York'})+' ET' : '—';
  let data, page=0, filtered=[];
  const size=50;
  const cell = (v, small) => '<td>'+esc(v)+(small?'<small>'+esc(small)+'</small>':'')+'</td>';
  const margin = (v,r) => v == null ? '—' : (Number(v)>=0?r.home:r.away)+' by '+number(Math.abs(v));
  function render() {
    const kind=$('kind').value, week=$('week').value, state=$('status').value;
    const query=$('search').value.trim().toLowerCase(), stat=$('stat').value;
    $('statLabel').hidden=kind!=='players';
    filtered=(data[kind]||[]).filter(r => (!week||String(r.week)===week) &&
      (!state||r.status===state) && (!$('version').value||(r.model_version||r.model_lineage||'')===$('version').value) && (kind!=='players'||!stat||r.stat===stat) &&
      (!query||[r.home,r.away,r.game_key,r.player,r.team,r.opponent,r.model_version]
        .filter(Boolean).join(' ').toLowerCase().includes(query)))
      .sort((a,b)=>String(b.kickoff||'').localeCompare(String(a.kickoff||'')));
    page=Math.min(page,Math.max(0,Math.ceil(filtered.length/size)-1));
    const rows=filtered.slice(page*size,(page+1)*size);
    let heads, body;
    if (kind==='games') {
      heads=['Week / matchup','Saved model margin','Final / actual margin','Model / actual total','ATS / total result','Saved / version'];
      body=rows.map(r=>'<tr>'+cell('W'+r.week+' · '+r.away+' at '+r.home,time(r.kickoff))+
        cell(margin(r.model_margin,r),r.book_margin==null?'No posted line':r.book+' home spread '+signed(-r.book_margin))+
        cell(r.actual_margin==null?'Pending':margin(r.actual_margin,r),r.model_abs_error==null?'':
          'Margin error '+number(r.model_abs_error)+' pts')+
        cell(number(r.model_total)+' / '+number(r.actual_total),r.model_total_abs_error==null?'':
          'Total error '+number(r.model_total_abs_error)+' pts')+
        cell((r.ats_result||'—')+' / '+(r.total_result||'—'),r.status)+
        cell(time(r.recorded_at),r.model_lineage||r.model_regime)+'</tr>').join('');
    } else if (kind==='players') {
      heads=['Week / player','Game / stat','Saved projection','Actual','Projection − actual','Saved / version'];
      body=rows.map(r=>'<tr>'+cell('W'+r.week+' · '+r.player,r.team+' · '+r.position)+
        cell(r.game_key,names[r.stat]||r.stat)+cell(number(r.projection),r.availability||'')+
        cell(number(r.actual),r.status==='pending'?'Pending':r.recorded_stat_line===false?'No recorded stat line':'Final')+
        cell(signed(r.error),r.error==null?'':'Absolute error '+number(Math.abs(r.error)))+
        cell(time(r.recorded_at),r.model_version)+'</tr>').join('');
    } else {
      heads=['Week / selection','Market / side','Entry line / price','Actual / result','Units / CLV','Recorded / type'];
      body=rows.map(r=>'<tr>'+cell('W'+r.week+' · '+(r.player_name||r.player||r.team||r.away+' at '+r.home),time(r.kickoff))+
        cell(r.market||r.stat||r.family,r.side)+cell(number(r.line)+' / '+signed(r.price),r.book||'')+
        cell(number(r.actual),r.result||r.status)+
        cell(r.price==null?'—':signed(r.units),r.clv==null?'CLV unavailable':'CLV '+signed(r.clv)+' pts')+
        cell(time(r.recorded_at),r.selection_type)+'</tr>').join('');
    }
    $('head').innerHTML='<tr>'+heads.map(h=>'<th scope="col">'+esc(h)+'</th>').join('')+'</tr>';
    $('body').innerHTML=body||'<tr><td colspan="6">No saved results match these filters.</td></tr>';
    const graded=filtered.filter(r=>r.status==='graded');
    let summary=filtered.length+' saved '+(kind==='players'?'stat forecasts':kind)+' · '+graded.length+' graded';
    if(kind==='games') {
      const errors=graded.filter(r=>r.model_abs_error!=null).map(r=>r.model_abs_error);
      if(errors.length)summary+=' · Margin MAE '+number(errors.reduce((a,b)=>a+b,0)/errors.length)+' pts';
    } else if(kind==='players'&&stat) {
      const errors=graded.filter(r=>r.error!=null).map(r=>Math.abs(r.error));
      if(errors.length)summary+=' · '+(names[stat]||stat)+' MAE '+number(errors.reduce((a,b)=>a+b,0)/errors.length);
    }
    $('summary').textContent=summary;
    $('pages').textContent=filtered.length ? (page*size+1)+'–'+Math.min((page+1)*size,filtered.length)+' of '+filtered.length : '0 results';
    $('previous').disabled=page===0;$('next').disabled=(page+1)*size>=filtered.length;
  }
  async function load() {
    try {
      const response=await fetch('../results.json?fresh='+Date.now(),{cache:'no-store'});
      if(!response.ok)throw new Error('Results unavailable');
      data=await response.json();
      const weeks=[...new Set([...data.games,...data.players,...data.wagers].map(r=>r.week))].sort((a,b)=>b-a);
      const old=$('week').value;
      $('week').innerHTML='<option value="">All weeks</option>'+weeks.map(w=>'<option value="'+w+'">Week '+w+'</option>').join('');
      $('week').value=old;
      const version=$('version').value;
      const versions=[...new Set([...data.games,...data.players].map(r=>r.model_version||r.model_lineage).filter(Boolean))].sort();
      $('version').innerHTML='<option value="">All versions</option>'+versions.map(v=>'<option value="'+esc(v)+'">'+esc(v)+'</option>').join('');
      $('version').value=version;
      $('asof').textContent=data.season+' season · Results updated '+time(data.generated_at);
      render();
    }catch(e){$('summary').textContent='Saved results could not be loaded. Reload to retry.';}
  }
  ['kind','week','status','stat','version'].forEach(id=>$(id).addEventListener('change',()=>{page=0;render();}));
  $('search').addEventListener('input',()=>{page=0;render();});
  $('previous').addEventListener('click',()=>{page--;render();});
  $('next').addEventListener('click',()=>{page++;render();});
  load();setInterval(()=>{if(!document.hidden)load();},300000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)load();});
})();
"""


def publish(history: dict, destination: Path, *, season: int) -> dict:
    data = payload(history, season=season)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "results.json").write_text(json.dumps(data, allow_nan=False), encoding="utf-8")
    from .site import _FONTS, _css

    folder = destination / "results"
    folder.mkdir(exist_ok=True)
    css = """
    .review-controls{display:flex;gap:14px;flex-wrap:wrap;margin:24px 0}
    .review-controls label{display:grid;gap:6px;min-width:140px;flex:1}
    .review-controls input,.review-controls select,.review-pages button{min-height:44px;
      background:var(--card);color:var(--text);border:1px solid var(--border);padding:10px;font:inherit;min-width:0;width:100%}
    .review-table{overflow:auto;border:1px solid var(--border)}
    .review-table table{width:100%;border-collapse:collapse;min-width:820px}
    .review-table th,.review-table td{padding:14px;text-align:left;vertical-align:top;
      border-bottom:1px solid var(--border);font-variant-numeric:tabular-nums}
    .review-table small{display:block;color:var(--text-2);margin-top:5px}
    .review-pages{display:flex;gap:16px;align-items:center;margin:20px 0}
    .review-pages button{width:auto}.review-pages button:disabled{opacity:.45}.review-note{color:var(--text-2);line-height:1.6}
    .review-nav{display:flex;gap:18px;flex-wrap:wrap;margin:22px 0}label[hidden]{display:none}
    """
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>CFB graded results — Chase Analytics</title>
{_FONTS}<style>{_css()}{css}</style></head><body><main class="wrap">
<nav class="review-nav"><a href="../">CFB model</a><a href="../tracking/">Weekly record &amp; CLV</a>
<a href="https://chase-analytics.com/model-center/?sport=cfb">Model Center</a><a href="../results.json" download>Download saved results</a></nav>
<h1>CFB graded predictions &amp; projections</h1><p id="asof"></p>
<p class="review-note">Review forecasts saved before kickoff against recorded outcomes. Games use the latest saved model snapshot;
player stats use the latest saved player projection. Wager entries keep their original line and price.</p>
<div class="review-controls"><label>Review<select id="kind"><option value="games">Game predictions</option>
<option value="players">Player projections</option><option value="wagers">Recorded selections</option></select></label>
<label>Week<select id="week"><option value="">All weeks</option></select></label>
<label>Status<select id="status"><option value="graded">Graded</option><option value="">All saved</option><option value="pending">Pending</option></select></label>
<label>Model version<select id="version"><option value="">All versions</option></select></label>
<label id="statLabel" hidden>Player stat<select id="stat"><option value="">All stats</option>
{"".join(f'<option value="{k}">{v}</option>' for k, v in {'pass_att': 'Pass attempts', 'pass_cmp': 'Completions', 'pass_yds': 'Pass yards', 'pass_td': 'Pass TDs', 'pass_int': 'Interceptions', 'rush_car': 'Carries', 'rush_yds': 'Rush yards', 'rec': 'Receptions', 'rec_yds': 'Receiving yards'}.items())}</select></label>
<label>Team or player<input id="search" type="search" placeholder="Search saved forecasts"></label></div>
<p id="summary" role="status">Loading saved results…</p>
<div class="review-table" tabindex="0" role="region" aria-label="Saved forecast results"><table><thead id="head"></thead><tbody id="body"></tbody></table></div>
<div class="review-pages"><button id="previous" type="button">Previous</button><span id="pages"></span><button id="next" type="button">Next</button></div>
<p class="review-note">A forecast error is a projection comparison, not a wager result. “No recorded stat line” is distinct from verified participation or a sportsbook void.
Missing historical prices and closing quotes stay unavailable. These are saved model outputs; new model versions do not rewrite old forecasts.</p>
</main><script>{_JS}</script></body></html>"""
    (folder / "index.html").write_text(page, encoding="utf-8")
    return data
