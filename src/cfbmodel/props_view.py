"""Published player matrix, availability context and identifiable book quotes."""
from __future__ import annotations

import json
from pathlib import Path


def attach_quotes(players: list[dict], quotes: list) -> None:
    from .sources.oddsapi import normalise
    for player in players:
        name = normalise(player.get("player_name", ""))
        game = player.get("game_key")
        matches = [q for q in quotes if normalise(q.player) == name
                   and game in (f"{q.away} at {q.home}", f"{q.away} @ {q.home}")]
        # Ambiguous alternate thresholds are not an identifiable main line.
        groups = {}
        for q in matches:
            groups.setdefault((q.stat, q.book), []).append(q)
        player["lines"] = [{"metric": stat, "line": values[0].line, "book": book,
                            "over_price": values[0].over_price, "under_price": values[0].under_price,
                            "quote_updated_at": values[0].last_update, "quote_status": "snapshot"}
                           for (stat, book), values in groups.items()
                           if len({q.line for q in values}) == 1]


JS = r"""
(() => {
const $=id=>document.getElementById(id), esc=v=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
const num=v=>v==null?'—':Number(v).toFixed(1), signed=v=>(v>0?'+':'')+num(v);
const labels={pass_yds:'Pass yards',pass_att:'Pass attempts',pass_cmp:'Completions',pass_td:'Pass TDs',pass_int:'Interceptions',rush_yds:'Rush yards',rush_car:'Carries',rec:'Receptions',rec_yds:'Receiving yards'};
let data,page=0;const size=20;
const time=v=>v?new Date(v).toLocaleString('en-US',{timeZone:'America/New_York'})+' ET':'Unavailable';
function render(){
 const search=$('search').value.toLowerCase(),pos=$('position').value,state=$('availability').value;
 const rows=data.players.filter(p=>(!pos||p.position===pos)&&(!state||(state==='uncertain'?(!p.profile?.availability_report||['questionable','game_time_decision','out_first_half'].includes(p.availability)):p.availability===state))&&
  [p.player_name,p.team,p.opponent,p.game_key].join(' ').toLowerCase().includes(search));
 page=Math.min(page,Math.max(0,Math.ceil(rows.length/size)-1));
 $('summary').textContent=rows.length+' players · Week '+data.week+' · '+data.status.model_version+' · Updated '+time(data.generated_at);
 $('cards').innerHTML=rows.slice(page*size,(page+1)*size).map(p=>{
 const profile=p.profile||{},context=profile.team_context||{};
 const report=profile.availability_report;
 const body=Object.entries(p.stats||{}).map(([stat,d])=>{
 const q=(p.lines||[]).find(q=>q.metric===stat), pred=p.predictability?.[stat]||{}, dev=profile.developments?.[stat]||{}, c=p.forecast_components?.[stat]||{};
 return '<tr><th scope="row">'+esc(labels[stat]||stat)+'</th><td><strong>'+num(d.mean)+'</strong><small>80% range '+num(d.p10)+'–'+num(d.p90)+'</small></td><td>'+(q?num(q.line)+'<small>'+esc(q.book)+' snapshot · '+esc(time(q.quote_updated_at))+'</small>':'Not posted')+'</td><td>'+(q?signed(d.mean-q.line)+'<small>Projection − line</small>':'—')+'</td><td>'+esc(pred.label)+'<small>'+esc(dev.direction)+'</small></td><td>'+num(c.historical_role)+' / '+signed(c.pool_normalization||0)+' / '+signed(c.availability_and_role_change||0)+'</td></tr>';
 }).join('');
 return '<article class="prop-card"><h2>'+esc(p.player_name)+' <small>'+esc(p.team)+' · '+esc(p.position)+' · '+esc(p.availability||'Not listed')+'</small></h2><p>'+esc(p.game_key)+' · '+esc(time(p.kickoff))+'</p><div class="prop-table" tabindex="0" role="region" aria-label="'+esc(p.player_name)+' projections"><table><thead><tr><th>Prop</th><th>Projection</th><th>Posted line</th><th>vs Line</th><th>Predictability</th><th>Historical / pool / availability</th></tr></thead><tbody>'+body+'</tbody></table></div><details><summary>Injury impact &amp; projection profile</summary><p>'+esc(report?'Conference report: '+report.conference+' · '+(report.published||'time unavailable'):'No current conference report; availability is unverified')+'</p><p>Projected team attempts: '+num(context.passing_attempts)+' pass / '+num(context.rushing_attempts)+' rush. Team margin '+signed(context.team_margin)+' · Total '+num(context.game_total)+'.</p><p>Work absorbed from: '+esc((p.absorbs||[]).join(', ')||'None identified')+'</p><ul>'+(profile.warnings||[]).map(w=>'<li>'+esc(w)+'</li>').join('')+'</ul><p>'+esc(profile.role_basis)+'</p><p>'+esc(profile.schematic_limits)+'</p></details></article>';
 }).join('')||'<p>No projections match these filters.</p>';
 $('pages').textContent=rows.length?(page*size+1)+'–'+Math.min((page+1)*size,rows.length)+' of '+rows.length:'0';$('previous').disabled=page===0;$('next').disabled=(page+1)*size>=rows.length;
}
async function load(){try{const r=await fetch('../player-props.json?fresh='+Date.now(),{cache:'no-store'});if(!r.ok)throw Error();data=await r.json();render();}catch(e){$('summary').textContent='Projections could not be loaded. Reload to retry.';}}
['position','availability'].forEach(id=>$(id).addEventListener('change',()=>{page=0;render();}));$('search').addEventListener('input',()=>{page=0;render();});$('previous').addEventListener('click',()=>{page--;render();});$('next').addEventListener('click',()=>{page++;render();});
load();setInterval(()=>{if(!document.hidden)load();},300000);
})();
"""


def publish(players: list[dict], status: dict, destination: Path, *, season: int,
            week: int, generated_at: str) -> None:
    from .site import _css, _FONTS
    (destination / "player-props.json").write_text(json.dumps({"season": season, "week": week,
        "generated_at": generated_at, "status": status, "players": players}, allow_nan=False), encoding="utf-8")
    folder = destination / "props"
    folder.mkdir(exist_ok=True)
    css = """.prop-nav,.prop-controls,.prop-pages{display:flex;gap:16px;flex-wrap:wrap;margin:24px 0}
    .prop-controls label{display:grid;gap:6px;flex:1;min-width:180px}.prop-controls input,.prop-controls select,.prop-pages button{min-height:44px;background:var(--card);color:var(--text);border:1px solid var(--border);font:inherit;padding:10px;min-width:0}
    .prop-card{background:var(--card);border:1px solid var(--border);padding:20px;margin:20px 0;min-width:0}.prop-card h2{font-size:20px}.prop-card small{display:block;color:var(--text-2);font-size:12px;margin-top:6px}
    .prop-table{overflow:auto;max-width:100%}table{border-collapse:collapse;min-width:900px;width:100%}td,th{padding:14px;text-align:left;border-bottom:1px solid var(--border);font-variant-numeric:tabular-nums}details{margin-top:18px;line-height:1.6}summary{cursor:pointer;min-height:44px;padding:10px 0}.prop-note{color:var(--text-2);line-height:1.6}.prop-pages button:disabled{opacity:.5}main{min-width:0}"""
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CFB player prop matrix — Chase Analytics</title>{_FONTS}<style>{_css()}{css}</style></head><body><main class="wrap">
<nav class="prop-nav"><a href="../">CFB model</a><a href="../results/">Graded results</a><a href="../tracking/">Weekly record &amp; CLV</a><a href="https://chase-analytics.com/model-center/?sport=cfb">Model Center · current lines</a></nav>
<h1>CFB player prop matrix</h1><p class="prop-note">Pre-kickoff projections with opportunity, injury impact and uncertainty. Posted lines are timestamped book snapshots. Open Model Center to check the current market. Missing lines stay unpriced.</p>
<div class="prop-controls"><label>Player or team<input type="search" id="search" placeholder="Search projections"></label><label>Position<select id="position"><option value="">All positions</option><option>QB</option><option>RB</option><option>WR</option><option>TE</option></select></label><label>Availability<select id="availability"><option value="">All projected players</option><option value="uncertain">Uncertain or unverified</option><option value="questionable">Questionable</option><option value="out_first_half">First-half absence</option></select></label></div>
<p id="summary" role="status">Loading projections…</p><div id="cards"></div><div class="prop-pages"><button id="previous">Previous</button><span id="pages"></span><button id="next">Next</button></div>
<p class="prop-note">Historical / pool / availability columns add to the final mean. Predictability reflects observed role variation and outcome ranges; it is not a wager win probability. Progression and regression labels compare the projection with recent output. These labels do not prove luck or causality.</p>
</main><script>{JS}</script></body></html>"""
    (folder / "index.html").write_text(page, encoding="utf-8")
