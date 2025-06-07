import * as THREE from "three";
import { CSS3DRenderer, CSS3DObject } from "three/examples/jsm/renderers/CSS3DRenderer.js";
import { TWEEN } from "@tweenjs/tween.js";

const input     = document.getElementById("mv-input");
const btn       = document.getElementById("mv-btn");
const voiceBtn  = document.getElementById("voice-btn");
const toastEl   = document.getElementById("toast-loading");
const toast     = new bootstrap.Toast(toastEl);
const container = document.getElementById("mv-container");

const W = container.clientWidth, H = container.clientHeight;
const scene  = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(55, W/H, 1, 6000);
camera.position.set(0, 300, 900);

const renderer = new THREE.WebGLRenderer({ antialias:true, alpha:true });
renderer.setSize(W, H);
container.appendChild(renderer.domElement);

const cssRenderer = new CSS3DRenderer();
cssRenderer.setSize(W, H);
cssRenderer.domElement.style.position = "absolute";
cssRenderer.domElement.style.top      = "0";
container.appendChild(cssRenderer.domElement);

const stars = (() => {
  const cnt = 4000;
  const g   = new THREE.BufferGeometry();
  const p   = new Float32Array(cnt*3);
  for (let i=0;i<p.length;i++) p[i] = (Math.random()-0.5)*5000;
  g.setAttribute("position", new THREE.BufferAttribute(p,3));
  return new THREE.Points(g, new THREE.PointsMaterial({ color:0xffffff, size:0.6 }));
})();
scene.add(stars);

const NODE_CNT = 400;
const nodes = [];
for (let i=0;i<NODE_CNT;i++){
  nodes.push(new THREE.Vector3(
    (Math.random()-0.5)*2000,
    (Math.random()-0.5)*1200,
    (Math.random()-0.5)*2000
  ));
}
const linkPos = [];
for (let i=0;i<NODE_CNT*2;i++){
  const a = nodes[Math.floor(Math.random()*NODE_CNT)];
  const b = nodes[Math.floor(Math.random()*NODE_CNT)];
  linkPos.push(a.x,a.y,a.z,b.x,b.y,b.z);
}
const linkGeo = new THREE.BufferGeometry();
linkGeo.setAttribute("position", new THREE.Float32BufferAttribute(linkPos,3));
scene.add(new THREE.LineSegments(
  linkGeo,
  new THREE.LineBasicMaterial({ color:0xffffff, opacity:0.6, transparent:true })
));
scene.add(new THREE.Points(
  new THREE.BufferGeometry().setFromPoints(nodes),
  new THREE.PointsMaterial({ color:0xffffff, size:3 })
));

(function animate(t){
  requestAnimationFrame(animate);
  scene.rotation.y += 0.0005;
  TWEEN.update(t);
  renderer.render(scene, camera);
  cssRenderer.render(scene, camera);
})();

const panels = [];
const hover  = el => {
  el.style.transition = "transform .2s";
  el.onmouseenter = ()=> el.style.transform="scale(1.05)";
  el.onmouseleave = ()=> el.style.transform="scale(1)";
};

async function loadNews(sym){
  toast.show();
  panels.forEach(p=>scene.remove(p));
  panels.length = 0;

  try{
    const res = await fetch(`/api/metaverse_feed?symbol=${encodeURIComponent(sym)}`);
    const tp  = res.headers.get("Content-Type") || "";
    if(!res.ok || !tp.includes("application/json")){
      window.location.href = "/login"; return;
    }
    const data = await res.json();
    if(data.error){ alert(data.error); return; }

    data.forEach((d,i)=>{
      const card = document.createElement("div");
      card.className = "card bg-dark text-light";
      card.style.width="240px"; card.style.padding=".6rem";
      card.innerHTML = `<h6 style="font-size:15px;margin-bottom:.4rem;">${d.title}</h6>
                        <small class="text-muted">${d.published}</small>`;
      card.onclick = ()=> window.open(d.link,"_blank");
      hover(card);

      const obj = new CSS3DObject(card);
      scene.add(obj); panels.push(obj);

      const end = nodes[Math.floor(Math.random()*NODE_CNT)];
      obj.position.set(0,0,0);
      new TWEEN.Tween(obj.position)
        .to({x:end.x,y:end.y,z:end.z},1200+i*120)
        .easing(TWEEN.Easing.Elastic.Out).start();
    });

    if(data[0] && "speechSynthesis" in window){
      speechSynthesis.speak(
        new SpeechSynthesisUtterance(`已載入 ${sym} 新聞，第一條：${data[0].title}`));
    }
  }catch(e){
    console.log(e); alert("取得資料時發生錯誤");
  }finally{ toast.hide(); }
}

btn.addEventListener("click", ()=>{
  const s = input.value.trim();
  if(!s) return alert("請輸入標的");
  loadNews(s);
});
if("SpeechRecognition" in window || "webkitSpeechRecognition" in window){
  const SR  = window.SpeechRecognition || window.webkitSpeechRecognition;
  const rec = new SR(); rec.lang="zh-TW";
  rec.onresult = e=>{
    const txt = e.results[0][0].transcript;
    input.value=txt; loadNews(txt);
  };
  voiceBtn.addEventListener("click", ()=>rec.start());
}else voiceBtn.style.display="none";
