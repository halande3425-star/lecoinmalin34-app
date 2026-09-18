
const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
async function load(){
  try{
    const [t,n]=await Promise.all([fetch('/api/topics').then(r=>r.json()),fetch('/api/new?limit=20').then(r=>r.json())]);
    document.querySelector('#topics').innerHTML=t.topics.map(x=>`
      <a class="card" href="https://t.me/Lecoinmalin34/${x.id}" target="_blank">
        <strong>${esc(x.title)}</strong>
        <div class="meta">📷 ${x.photos} &nbsp; 🎬 ${x.videos} &nbsp; 📝 ${x.texts}</div>
      </a>`).join('');
    document.querySelector('#new').innerHTML=n.items.length?n.items.map(x=>`
      <a class="card" href="https://t.me/Lecoinmalin34/${x.message_id}" target="_blank">
        <strong>${esc(x.topic)}</strong><div class="meta"><span class="badge">NOUVEAU</span> • Voir le produit</div>
      </a>`).join(''):'<div class="meta">Les prochains produits ajoutés apparaîtront ici automatiquement.</div>';
    document.querySelector('#sync').textContent='À jour';
  }catch(e){document.querySelector('#sync').textContent='Actualisation en cours';}
}
load(); setInterval(load,30000);
if('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(()=>{});
