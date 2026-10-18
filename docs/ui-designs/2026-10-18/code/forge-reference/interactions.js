
'use strict';
const state = { mode: 'quick', section: 'identity', destination: 'C1', theme: 'High fantasy', name: '', role: 'NPC', species: '', class: '', powerSlot: 'level', level: '', cr: '', concept: '', background: '', personality: '', secret: '', rumor: '', partyHook: '', appearance: '', goals: '', relationships: '', voiceStyle: '', catchphrases: '', toggles: {}, skills: [], actions: [], traits: [], spells: [], relations: { C1: [], C2: [] }, activeChip: null };
const destinationNames = { C1: 'The Reapened Coast', C2: 'The Ember Marches', Generic: 'Generic library' };
const entities = { C1: ['Saltmere (place)', 'Tidekeepers (faction)', 'Ilyra Voss (character)'], C2: ['Cinderwatch (place)', 'Ember Covenant (faction)', 'Tovan Ash (character)'] };
const fieldLabels = { name: 'Name', role: 'Role', species: 'Species', class: 'Class', alignment: 'Alignment', concept: 'Concept', background: 'Background', personality: 'Personality', secret: 'Secret', rumor: 'Rumor', partyHook: 'Party hook', appearance: 'Appearance', goals: 'Goals', relationships: 'Relationships', voiceStyle: 'Voice style', catchphrases: 'Catchphrases', reputation: 'Reputation', factions: 'Factions', currentLocation: 'Current location', reactionMatrix: 'Reaction matrix', onDefeat: 'On defeat' };
const $ = id => document.getElementById(id);
function put(el, text) { el.textContent = text; return el; }
function node(tag, text, cls) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (cls) el.className = cls; return el; }
function authoredEntries() {
  const entries = Object.entries(fieldLabels).filter(([k]) => String(state[k] || '').trim()).map(([k, l]) => [l, state[k]]);
  if (state.powerSlot === 'level' && state.level) entries.push(['Level', state.level]);
  if (state.powerSlot === 'cr' && state.cr) entries.push(['CR', state.cr]);
  const groups = { attributes: ['str', 'dex', 'con', 'int', 'wis', 'cha'], combat: ['ac', 'hp', 'hitDiceCount', 'hitDiceSides', 'hitDiceMod'], boss: ['lairActions', 'legendaryActions', 'immunities', 'vulnerabilities'] };
  for (const [group, keys] of Object.entries(groups)) if (state.toggles[group] && (group !== 'boss' || state.role !== 'NPC')) for (const key of keys) if (String(state[key] || '').trim()) entries.push([key.replace(/([A-Z])/g, ' $1'), state[key]]);
  for (const key of ['skills', 'actions', 'traits', 'spells']) if (state.toggles[key]) for (const row of state[key]) if (Object.values(row).some(v => String(v ?? '').trim())) entries.push([key[0].toUpperCase() + key.slice(1), Object.entries(row).filter(([, v]) => String(v ?? '').trim()).map(([k, v]) => k === 'name' ? v : `${k}: ${v}`).join(' · ')]);
  if (state.destination !== 'Generic') for (const rel of state.relations[state.destination]) if (rel.target) entries.push(['Relation', `${rel.type.replaceAll('_', ' ')} → ${rel.target} (${rel.mode})`]);
  return entries;
}
function updateSummary() {
  document.querySelectorAll('[data-destination-name]').forEach(el => put(el, destinationNames[state.destination]));
  $('theme-control').hidden = state.destination !== 'Generic';
  document.querySelector('[data-world-relations]').hidden = state.destination === 'Generic';
  document.querySelector('[data-generic-relations]').hidden = state.destination !== 'Generic';
  document.querySelector('[data-boss-wrap]').hidden = state.role === 'NPC';
  document.querySelector('[data-boss-note]').hidden = state.role !== 'NPC';
  document.querySelectorAll('[data-power]').forEach(el => el.hidden = el.dataset.power !== state.powerSlot);
  document.querySelectorAll('[data-role]').forEach(el => {
    const selected = el.dataset.role === state.role; el.classList.toggle('selected', selected); el.setAttribute('aria-pressed', String(selected));
  });
  const supplied = $('summary-supplied'); supplied.replaceChildren();
  for (const [label, value] of authoredEntries()) supplied.append(node('li', `${label}: ${value}`));
  const generated = $('summary-generated'); generated.replaceChildren();
  const missing = [];
  for (const [key, label] of [['name', 'Name'], ['species', 'Species'], ['class', 'Class'], [state.powerSlot, 'Level / CR'], ['background', 'Background'], ['appearance', 'Appearance']]) if (!String(state[key] || '').trim()) missing.push(label);
  if (!['attributes', 'combat', 'skills', 'actions', 'traits', 'spells'].some(k => state.toggles[k])) missing.push('Full stat block'); else missing.push('Remaining unspecified mechanics');
  if (!state.concept && !state.background) missing.push('Unspecified character details');
  missing.forEach(label => generated.append(node('li', '• ' + label)));
  put($('generate-destination'), `Builds a new character into ${destinationNames[state.destination]}.`);
  $('generate-btn').disabled = !state.name.trim() && !state.concept.trim() && !state.background.trim();
  const review = $('review-list'); review.replaceChildren(node('dt', 'Destination'), node('dd', destinationNames[state.destination]));
  if (state.destination === 'Generic') review.append(node('dt', 'Theme'), node('dd', state.theme));
  for (const [label, value] of authoredEntries()) review.append(node('dt', label), node('dd', String(value)));
  if ($('generate-btn').disabled) put($('generate-destination'), `Add a name, concept or authored background to preview generation into ${destinationNames[state.destination]}.`);
  
  document.querySelectorAll('.char-chip').forEach(chip => {
    const key = chip.dataset.chip;
    const hasContent = String(state[key] || '').trim().length > 0;
    chip.classList.toggle('has-content', hasContent);
    chip.classList.toggle('active', state.activeChip === key);
  });
  document.querySelectorAll('.chip-panel').forEach(panel => {
    panel.hidden = panel.id !== `panel-${state.activeChip}`;
  });
}
function syncFields(key) {
  document.querySelectorAll('[data-field]').forEach(el => {
    if (el.dataset.field === key && el !== document.activeElement) el.value = state[key] || '';
  });
}
function activateMode(mode, focus = false) {
  state.mode = mode;
  document.querySelectorAll('[data-mode]').forEach(tab => {
    const on = tab.dataset.mode === mode;
    tab.setAttribute('aria-selected', String(on));
    tab.tabIndex = on ? 0 : -1;
    if (on && focus) tab.focus();
  });
  $('panel-quick').hidden = mode !== 'quick';
  $('panel-detailed').hidden = mode !== 'detailed';
}
function activateSection(section, focus = false) {
  state.section = section;
  document.querySelectorAll('[data-section]').forEach(tab => {
    const on = tab.dataset.section === section;
    tab.setAttribute('aria-selected', String(on));
    tab.tabIndex = on ? 0 : -1;
    $('detail-' + tab.dataset.section).hidden = !on;
    if (on && focus) tab.focus();
  });
  updateSummary();
}
function setupTabs(selector, activate, key) {
  const tabs = [...document.querySelectorAll(selector)];
  tabs.forEach((tab, i) => {
    tab.addEventListener('click', () => activate(tab.dataset[key]));
    tab.addEventListener('keydown', event => {
      let index;
      if (event.key === 'ArrowRight') index = (i + 1) % tabs.length;
      else if (event.key === 'ArrowLeft') index = (i + tabs.length - 1) % tabs.length;
      else if (event.key === 'Home') index = 0;
      else if (event.key === 'End') index = tabs.length - 1;
      else return;
      event.preventDefault();
      activate(tabs[index].dataset[key], true);
    });
  });
}
setupTabs('[data-mode]', activateMode, 'mode'); setupTabs('[data-section]', activateSection, 'section');

document.querySelectorAll('.char-chip').forEach(chip => {
  chip.addEventListener('click', () => {
    const key = chip.dataset.chip;
    if (state.activeChip === key) {
      state.activeChip = null;
    } else {
      state.activeChip = key;
    }
    updateSummary();
    if (state.activeChip) {
      const input = document.querySelector(`#panel-${state.activeChip} [data-field]`);
      if (input) input.focus();
    }
  });
});

document.addEventListener('input', event => {
  const el = event.target;
  if (el.dataset.field) {
    state[el.dataset.field] = el.value;
    syncFields(el.dataset.field);
    $('generate-status').hidden = true;
    updateSummary();
  }
});
document.addEventListener('change', event => {
  const el = event.target;
  if (el.dataset.field) {
    state[el.dataset.field] = el.value;
    syncFields(el.dataset.field);
    updateSummary();
  }
  if (el.dataset.toggle) {
    state.toggles[el.dataset.toggle] = el.checked;
    $('optional-' + el.dataset.toggle).hidden = !el.checked;
    updateSummary();
  }
});
document.querySelectorAll('[data-role]').forEach(el => el.addEventListener('click', () => { state.role = el.dataset.role; syncFields('role'); updateSummary(); }));
function rowField(label, value, onChange, options) {
  const wrap = node('label', undefined, 'field');
  wrap.append(node('span', label, 'field-label'));
  const multiline = label === 'Description'; const input = node(options ? 'select' : multiline ? 'textarea' : 'input', undefined, options ? 'forge-select' : multiline ? 'forge-textarea' : 'forge-input');
  if (multiline) input.rows = 3;
  if (!options && !multiline) input.type = 'text';
  if (options) for (const [val, text] of options) { const option = node('option', text); option.value = val; input.append(option); }
  input.value = value || '';
  input.addEventListener(options ? 'change' : 'input', () => { onChange(input.value); updateSummary(); });
  wrap.append(input);
  return wrap;
}
function renderList(key, focusNew = false) {
  const host = $('rows-' + key); host.replaceChildren();
  state[key].forEach((row, i) => {
    const wrap = node('div', undefined, 'list-row');
    const grid = node('div', undefined, 'fields-grid');
    const fields = key === 'skills' ? ['name', 'bonus', 'description'] : key === 'actions' ? ['name', 'description', 'damageCount', 'damageSides', 'damageMod'] : key === 'spells' ? ['name', 'description'] : ['name', 'description'];
    if(key === 'spells') { grid.classList.add('spell-fields'); wrap.classList.add('spell-row'); }
    for (const name of fields) grid.append(rowField(key === 'spells' && name === 'name' ? 'Spell name' : name.replace(/([A-Z])/g, ' $1').replace(/^./, c => c.toUpperCase()), row[name], v => row[name] = v));
    wrap.append(grid);
    const actions = node('div', undefined, 'row-action');
    const remove = node('button', 'Remove ' + key.slice(0, -1), 'link-button');
    remove.type = 'button'; remove.setAttribute('aria-label', 'Remove ' + key.slice(0, -1) + ' ' + (i + 1));
    remove.addEventListener('click', () => { state[key].splice(i, 1); renderList(key); updateSummary(); document.querySelector('[data-add="' + key + '"]').focus(); });
    actions.append(remove);
    wrap.append(actions);
    host.append(wrap);
  });
  if (focusNew) host.lastElementChild?.querySelector('input')?.focus();
}
document.querySelectorAll('[data-add]').forEach(button => button.addEventListener('click', () => { state[button.dataset.add].push({}); renderList(button.dataset.add, true); }));
const edgeTypes = ['relationship', 'debt', 'grudge', 'loyalty', 'member_of', 'located_in', 'rival_of', 'kin_of', 'ally_of', 'enemy_of', 'bases_at', 'controls', 'employs', 'worships', 'hails_from', 'protects'];
function renderRelations(focusNew = false) {
  const host = $('relation-rows'); host.replaceChildren(); if (state.destination === 'Generic') return;
  const rows = state.relations[state.destination];
  if (!rows.length) host.append(node('p', 'No relations authored for this world.', 'empty-relations'));
  rows.forEach((row, i) => {
    const wrap = node('div', undefined, 'relation-row');
    const grid = node('div', undefined, 'fields-grid');
    grid.append(rowField('Relation type', row.type, v => row.type = v, edgeTypes.map(x => [x, x.replaceAll('_', ' ')])));
    grid.append(rowField('Target mode', row.mode, v => { row.mode = v; row.target = ''; renderRelations(); updateSummary(); host.children[i]?.querySelectorAll('select')[1]?.focus(); }, [['existing', 'Existing entity'], ['new', 'Create new']]));
    grid.append(rowField(row.mode === 'existing' ? 'Committed entity' : 'New target name', row.target, v => row.target = v, row.mode === 'existing' ? [['', 'Choose an entity'], ...entities[state.destination].map(x => [x, x])] : null));
    wrap.append(grid);
    const remove = node('button', 'Remove relation', 'link-button');
    remove.type = 'button';
    remove.addEventListener('click', () => { rows.splice(i, 1); renderRelations(); updateSummary(); $('add-relation').focus(); });
    wrap.append(remove);
    host.append(wrap);
  });
  if (focusNew) host.lastElementChild?.querySelector('select')?.focus();
}
$('add-relation').addEventListener('click', () => { if (state.destination === 'Generic') return; state.relations[state.destination].push({ type: 'located_in', mode: 'existing', target: '' }); renderRelations(true); updateSummary(); });
$('change-dest-btn').addEventListener('click', () => { const open = $('destination-picker').hidden; $('destination-picker').hidden = !open; $('change-dest-btn').setAttribute('aria-expanded', String(open)); if (open) $('destination-select').focus(); });
function closeDestination() { $('destination-picker').hidden = true; $('change-dest-btn').setAttribute('aria-expanded', 'false'); $('change-dest-btn').focus(); }
$('destination-done').addEventListener('click', closeDestination);
$('destination-picker').addEventListener('keydown', e => { if (e.key === 'Escape') { e.preventDefault(); closeDestination(); } });
$('destination-select').addEventListener('change', e => { state.destination = e.target.value; $('generate-status').hidden = true; renderRelations(); updateSummary(); });
$('theme-select').addEventListener('change', e => { state.theme = e.target.value; updateSummary(); });
const history = $('history-dialog'); $('history-btn').addEventListener('click', () => history.showModal()); $('close-history').addEventListener('click', () => history.close()); history.addEventListener('close', () => $('history-btn').focus());
$('example-cancel').addEventListener('click', () => { $('cancel-status').hidden = false; $('example-cancel').disabled = true; put($('example-cancel'), 'Example cancelled'); });
$('generate-btn').addEventListener('click', () => { if (!state.name.trim() && !state.concept.trim() && !state.background.trim()) return; $('generate-status').hidden = false; put($('generate-status'), `Preview ready for ${state.name.trim() || 'your character'} in ${destinationNames[state.destination]}. This demonstration sends no request and saves nothing.`); });
$('preview-logout').addEventListener('click', () => { $('logout-status').hidden = false; });
renderRelations(); updateSummary();


const identityHints = {"species": {"": "Suggests appearance, heritage and perspective; leave open for generation.", "Dragonborn": "Draconic heritage and scaled appearance; guides ancestry and presence.", "Dwarf": "Sturdy build and deep traditions; suggests craft, duty and long-held ties.", "Elf": "A long-lived perspective; suggests grace, keen senses and enduring memories.", "Gnome": "Small, inventive and curious; suggests clever solutions and playful detail.", "Half-Elf": "Elven and human heritage; suggests life between different communities.", "Half-Orc": "Human and orc ancestry; suggests an imposing appearance and questions of belonging.", "Halfling": "Small stature and resourcefulness; suggests close communities and everyday courage.", "Human": "Versatile heritage; leaves room for varied cultures, motives and professions.", "Tiefling": "Infernal ancestry; guides unusual appearance and how others might respond."}, "class": {"": "Guides training, combat style, skills and spellcasting; leave open for generation.", "Barbarian": "Ferocious martial strength; guides resilience, close combat and temperament.", "Bard": "Performance and versatile magic; guides social skills and expressive character details.", "Cleric": "Divine magic and faith; guides beliefs, sacred duties and spell choices.", "Druid": "Nature-focused magic; guides wilderness ties, traditions and spell choices.", "Fighter": "Martial expertise; guides weapon tactics, training and combat background.", "Monk": "Disciplined martial training; guides movement, unarmed fighting and outlook.", "Paladin": "An oath-driven warrior; guides martial abilities, divine magic and ideals.", "Ranger": "Wilderness expertise and martial training; guides survival skills and tactics.", "Rogue": "Skill, stealth and precision; guides expertise, tactics and secrets.", "Sorcerer": "Innate arcane power; guides spellcasting and the source of the character’s magic.", "Warlock": "Magic linked to a patron; guides the pact, powers and obligations.", "Wizard": "Study-driven arcane magic; guides spell choices, lore and preparation."}};
let identityChoiceKey = 'species';
let identityChoiceTrigger = null;
const originalUpdateSummary = updateSummary;
function refinementUpdate() {
 document.querySelectorAll('[data-choice-hint]').forEach(el => {const key=el.dataset.choiceHint;el.textContent=identityHints[key][state[key] || ''];});
 document.querySelectorAll('[data-chip]').forEach(chip => {const key=chip.dataset.chip;chip.setAttribute('aria-expanded',String(state.activeChip===key));chip.querySelector('[data-detail-filled]').textContent=String(state[key] || '').trim()?'Filled':'';});
 const groups={attributes:['str','dex','con','int','wis','cha'],combat:['ac','hp','hitDiceCount','hitDiceSides','hitDiceMod'],boss:['lairActions','legendaryActions','immunities','vulnerabilities']};
 document.querySelectorAll('[data-mechanic-count]').forEach(el=>{const key=el.dataset.mechanicCount;const count=groups[key]?groups[key].filter(k=>String(state[k] || '').trim()).length:(state[key] || []).filter(row=>Object.values(row).some(v=>String(v ?? '').trim())).length;el.textContent=state.toggles[key]?(count?count+' authored':'Ready to author'):(count?count+' saved':'Will generate');el.closest('.toggle-section').classList.toggle('is-open',!!state.toggles[key]);});
}
updateSummary = function(){originalUpdateSummary();refinementUpdate();};
const identityDialog=document.getElementById('identity-choice-dialog');
document.querySelectorAll('[data-explore]').forEach(button=>button.addEventListener('click',()=>{
 identityChoiceKey=button.dataset.explore;identityChoiceTrigger=button;
 document.getElementById('identity-choice-title').textContent=identityChoiceKey==='species'?'Choose a species':'Choose a class';
 const host=document.getElementById('identity-choice-grid');host.replaceChildren();
 for(const [value,description] of Object.entries(identityHints[identityChoiceKey])){const choice=node('button',undefined,'identity-choice');choice.type='button';choice.dataset.choiceValue=value;choice.setAttribute('aria-pressed',String(state[identityChoiceKey]===value));choice.append(node('strong',value || 'Let the model choose'),node('small',description));choice.addEventListener('click',()=>{state[identityChoiceKey]=value;syncFields(identityChoiceKey);updateSummary();identityDialog.close();});host.append(choice);}
 identityDialog.showModal();
}));
document.getElementById('close-identity-choice').addEventListener('click',()=>identityDialog.close());
identityDialog.addEventListener('close',()=>identityChoiceTrigger?.focus());
updateSummary();

