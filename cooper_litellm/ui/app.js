'use strict';
let state, editing, revision;
const $ = id => document.getElementById(id);
const labels = {ready:'正常',invalid:'秘钥无效',exhausted:'额度耗尽',cooldown:'暂时冷却',unavailable:'发现失败',not_loaded:'待载入'};
function element(tag, text, cls) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; }
function notify(text, bad=false) { $('notice').hidden=false; $('notice').textContent=text; $('notice').className=bad?'failure':''; }
async function api(action, body={}) { const r=await fetch('/api/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},body:JSON.stringify(body)}); const data=await r.json(); if(!r.ok) throw new Error(data.error || '操作失败'); return data; }
async function refresh() {
  const r=await fetch('/api/state',{cache:'no-store'}); if(!r.ok) throw new Error('管理层状态读取失败'); state=await r.json();
  $('service').textContent=state.health.ready?'运行中':'未就绪';
  $('generation').textContent=state.health.generation?'版本 '+state.health.generation.slice(0,8):'原生回退仍可使用';
  $('count').textContent=state.profiles.length; $('models').textContent=state.health.models.length;
  $('mode').textContent={managed:'管理网关',native:'原生 Codex',other:'其他接入',unknown:'未确认'}[state.mode];
  const cards=$('cards'); cards.replaceChildren();
  for(const profile of state.profiles){
    const card=element('article',undefined,'card'); const head=element('div',undefined,'card-title'); head.append(element('h3',profile.display_name||profile.id));
    head.append(element('span',profile.enabled?(labels[profile.state.status]||profile.state.status):'已停用','badge'+(profile.state.status!=='ready'||!profile.enabled?' warn':''))); card.append(head);
    card.append(element('div',profile.api_base,'endpoint'));
    const metrics=element('div',undefined,'card-metrics'); for(const [n,label] of [[profile.models.length,'可调用模型'],[profile.models.filter(m=>profile.visible.includes(m.slice(profile.prefix.length))).length,'前端可见']]){const box=element('div');box.append(element('strong',n),element('span',label));metrics.append(box);}card.append(metrics);
    card.append(element('div','ID：'+profile.id+' · 秘钥：'+(profile.has_secret?'已加密保存 · 不回显':'尚未导入'),'card-meta'),element('div','备用：'+(profile.fallback_keys.join(' → ')||'不跨接入切换'),'card-meta'));
    const buttons=element('div',undefined,'card-buttons');const edit=element('button','管理接入');edit.onclick=()=>openEditor(profile);buttons.append(edit);
    const retry=element('button','重试','quiet');retry.onclick=()=>perform('reset',{id:profile.id},'已允许重新尝试此秘钥');buttons.append(retry);
    const remove=element('button','移除','quiet');remove.onclick=()=>{if(confirm('从路由中移除 '+profile.id+'？其加密秘钥文件将保留用于恢复，在途请求继续完成。'))perform('remove',{id:profile.id,revision:state.revision},'已移除配置；加密秘钥仍保留，可恢复。');};buttons.append(remove);card.append(buttons);cards.append(card);
  }
  if(!state.profiles.length)cards.append(element('p','还没有接入。导入秘钥后，系统会从上游发现模型。'));
  if(state.health.last_error)notify('当前状态：'+state.health.last_error.code+'。若热更新被拒绝，上一代仍继续服务。',true);
}
function choice(container,value,label,checked){const row=element('label');const input=element('input');input.type='checkbox';input.value=value;input.checked=checked;row.append(input,document.createTextNode(label));container.append(row);}
function openEditor(profile){editing=profile||null;revision=state.revision;$('profile-form').reset();$('editor-title').textContent=profile?'管理 '+profile.id:'导入新接入';$('display-name').value=profile?.display_name||'';$('default-mode').value=profile?.default_mode||'auto';$('id').value=profile?.id||'';$('id').disabled=!!profile;$('prefix').value=profile?.prefix||'';$('api-base').value=profile?.api_base||'';$('secret').value='';$('secret').required=!profile;$('enabled').checked=profile?.enabled??true;$('save-status').textContent='';
 $('fallbacks').replaceChildren();for(const p of state.profiles)if(p.id!==profile?.id)choice($('fallbacks'),p.id,p.display_name||p.id,profile?.fallback_keys.includes(p.id)||false);
 if(!$('fallbacks').children.length)$('fallbacks').append(element('p','导入其他接入后可设置备用。'));
 $('visibility').replaceChildren();const models=new Set([...(profile?.models||[]),...(profile?.visible||[]).map(v=>profile.prefix+v)]);for(const alias of models){const short=alias.slice(profile.prefix.length);choice($('visibility'),short,alias,profile.visible.includes(short));}
 if(!models.size)$('visibility').append(element('p','导入后自动获取模型。新接入默认全部隐藏，可稍后调整。'));
 $('editor').showModal();}
async function perform(action,body,message){try{await api(action,body);notify(message);await refresh();}catch(e){notify(e.message,true);}}
$('add').onclick=()=>openEditor(null);$('close').onclick=()=>$('editor').close();$('editor').addEventListener('close',()=>{$('secret').value='';});
$('id').oninput=()=>{if(!editing)$('prefix').value=$('id').value.toLowerCase()+'-';};
$('show-all').onclick=()=>{$('visibility').querySelectorAll('input').forEach(i=>i.checked=true);};$('hide-all').onclick=()=>{$('visibility').querySelectorAll('input').forEach(i=>i.checked=false);};
$('profile-form').onsubmit=async event=>{event.preventDefault();$('save').disabled=true;$('save-status').textContent='正在验证，请勿关闭…';const body={revision,display_name:$('display-name').value.trim()||$('id').value.trim(),default_mode:$('default-mode').value,id:$('id').value.trim(),prefix:$('prefix').value.trim(),api_base:$('api-base').value.trim(),enabled:$('enabled').checked,visible:[...$('visibility').querySelectorAll('input:checked')].map(i=>i.value),fallback_keys:[...$('fallbacks').querySelectorAll('input:checked')].map(i=>i.value)};if($('secret').value)body.secret=$('secret').value;$('secret').value='';try{await api('profile',body);delete body.secret;$('editor').close();notify('配置已保存，正在热更新。模型状态将在新一代验证通过后更新。');await refresh();}catch(e){delete body.secret;$('save-status').textContent=e.message+'；秘钥输入已清空，请重新输入。';}finally{$('save').disabled=false;}};
$('reload').onclick=()=>perform('reload',{},'已请求刷新上游目录；验证失败不会替换当前服务。');
$('native').onclick=()=>{if(confirm('切回原生 Codex 配置？将备份当前配置，不重启桌面、不删除秘钥或历史。'))perform('native',{},'已切回原生配置。已打开的桌面可能仍需重新加载；没有自动重启。');};
$('managed').onclick=()=>{if(confirm('将全局配置指向已验证的本机管理网关？当前配置会先定点备份。'))perform('managed',{},'已选择管理网关配置；已有桌面不会被自动重启。');};
refresh().catch(e=>notify(e.message,true));setInterval(()=>{if(!$('editor').open)refresh().catch(e=>notify(e.message,true));},5000);
