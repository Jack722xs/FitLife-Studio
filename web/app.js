"use strict";
const $ = id => document.getElementById(id);
const e = value => String(value ?? "—").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const money = value => new Intl.NumberFormat("es-CL", {style:"currency",currency:"CLP"}).format(value);
const dt = value => e(value?.replace("T", " ") ?? "—");
const selected = (a,b) => String(a) === String(b) ? " selected" : "";
const opt = (value, label, current) => `<option value="${e(value)}"${selected(value,current)}>${e(label)}</option>`;
const hidden = (name, value) => `<input type="hidden" name="${e(name)}" value="${e(value)}">`;
const field = (name,label,value="",type="text",attrs="required") => `<label class="field">${e(label)}<input name="${name}" value="${e(value)}" type="${type}" ${attrs}></label>`;
const select = (name,label,options) => `<label class="field">${e(label)}<select name="${name}">${options}</select></label>`;
const check = (name,label) => `<label class="small"><input type="checkbox" name="${name}" value="si" required>${e(label)}</label>`;
const button = (label,style="") => `<button class="${style}" type="submit">${e(label)}</button>`;
const card = (title, body) => `<section class="card"><h2>${e(title)}</h2>${body}</section>`;
const hero = (module,title,desc) => `<header class="hero"><div class="eyebrow">${e(module)}</div><h1>${e(title)}</h1><p class="subtle">${e(desc)}</p></header>`;
const form = (action, body, label, style="") => `<form data-action="${action}">${body}${button(label,style)}</form>`;
const detail = (label, body) => `<details class="details"><summary>${e(label)}</summary>${body}</details>`;
const badge = (value,style="") => `<span class="badge ${style}">${e(value)}</span>`;
const go = (label, href) => `<a class="button secondary" href="#${e(href)}">${e(label)}</a>`;
const displayTable = (headers, records, empty="Todavía no hay registros.") => records.length ?
  `<div class="table-wrap"><table><thead><tr>${headers.map(h=>`<th scope="col">${e(h)}</th>`).join("")}</tr></thead><tbody>${records.map(row=>`<tr>${row.map(cell=>`<td>${cell}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` :
  `<p class="small">${e(empty)}</p>`;

const roleLabels = {admin:"Administración",recepcion:"Recepción",entrenador:"Entrenador"};
const nav = [
  ["acceso","Control de acceso"], ["socios","Socios"], ["cobros","Membresías y cobros"], ["pausas","Congelamientos"],
  ["caja","Caja"], ["catalogo","Planes y convenios",true], ["informes","Informes",true],
  ["equipo","Usuarios y auditoría",true], ["contingencia","Contingencia"]
];
let user = null;
let offline = false;
let snapshot = null;
let viewNumber = 0;
let syncing = false;
let busy = false;
let latestAccess = null;
let found = "";
const storageKey = suffix => "fitlife-v1-" + suffix;
function readLocal(key, fallback) {
  try { return JSON.parse(localStorage.getItem(storageKey(key))) ?? fallback; }
  catch { return fallback; }
}
function saveLocal(key, value) { localStorage.setItem(storageKey(key), JSON.stringify(value)); }
const queueKey = () => "queue-" + user.id;
const queue = () => user ? readLocal(queueKey(), []) : [];
const hasLease = () => snapshot && snapshot.expires >= Math.floor(Date.now()/1000);
const pageId = () => location.hash.slice(1).split("/")[0] || (user?.role === "entrenador" ? "socios" : "acceso");
const targetId = () => Number(location.hash.split("/")[1] || 0);
const dateNow = () => {
  if (!offline && user?.day) return user.day;
  const date = new Date();
  return [date.getFullYear(), String(date.getMonth()+1).padStart(2,"0"), String(date.getDate()).padStart(2,"0")].join("-");
};
function notify(message, error=false) {
  $("notice").textContent = message;
  $("notice").className = error ? "error" : "";
  $("notice").hidden = !message;
}
async function api(path, data) {
  let response;
  try {
    response = await fetch("/api/" + path, {
      ...(data ? {method:"POST",headers:{"Content-Type":"application/json","X-CSRF":user?.csrf || ""},
                   body:JSON.stringify(data)} : {}),
      cache:"no-store", signal:AbortSignal.timeout(9000)
    });
  } catch (error) {
    throw Object.assign(new Error("No se pudo conectar con el servidor."), {network:true, cause:error});
  }
  const body = await response.json();
  if (!response.ok) throw Object.assign(new Error(body.error), {status:response.status});
  return body;
}
function navigation() {
  const path = pageId();
  const entries = !user ? [] : user.role === "entrenador" ? [["socios","Consulta de socios"]] :
    nav.filter(([key,,admin]) => offline ? ["acceso","cobros","contingencia"].includes(key) : !admin || user.role === "admin");
  $("nav").innerHTML = entries.map(([key,label], i) => `<a class="navlink" href="#${key}"${path===key?' aria-current="page"':""}><small>${String(i+1).padStart(2,"0")}</small>${e(label)}</a>`).join("");
  $("identity").textContent = user ? user.name + " · " + roleLabels[user.role] : "";
  $("logout").hidden = !user;
  $("network").className = "badge " + (offline ? "warn" : "");
  $("network").textContent = (offline ? "Sin conexión" : "Servidor local") + (queue().length ? " · " + queue().length + " en cola" : "");
}
async function refreshSession() {
  try {
    user = await api("session");
    offline = false;
    saveLocal("identity", {id:user.id, name:user.name, role:user.role});
  } catch (error) {
    if (!error.network) {
      user = null;
      snapshot = null;
      localStorage.removeItem(storageKey("identity"));
      if (error.status !== 401) throw error;
    } else {
      offline = true;
      user = readLocal("identity", null);
    }
  }
  snapshot = user && user.role !== "entrenador" ? readLocal("snapshot-" + user.id, null) : null;
}
async function prepareOffline() {
  if (!user || offline || user.role === "entrenador") return;
  snapshot = await api("snapshot");
  saveLocal("snapshot-" + user.id, snapshot);
}
function cleanRut(value) {
  const raw = value.toUpperCase().replace(/[.\s-]/g,"");
  if (!/^\d{1,8}[\dK]$/.test(raw)) throw new Error("Ingresa un RUT con dígito verificador.");
  const body = String(Number(raw.slice(0,-1)));
  const sum = [...body].reverse().reduce((n,x,i)=>n+Number(x)*(i%6+2),0);
  const dv = 11-sum%11;
  if (body === "0" || raw.at(-1) !== (dv===11 ? "0" : dv===10 ? "K" : String(dv))) throw new Error("RUT inválido.");
  return body + "-" + raw.at(-1);
}
function offlineAccess(identifier) {
  const today = dateNow();
  const periods = snapshot.periods.filter(p=>p.rut===identifier && p.starts<=today && p.ends>=today);
  const paused = p => snapshot.freezes.some(f=>f.membership_id===p.id && f.starts<=today && f.ends>=today);
  if (periods.some(p=>!paused(p))) return {local:"permitido",reason:"Vigente según la última copia."};
  if (periods.length) return {local:"denegado",reason:"Congelado según la última copia."};
  if (snapshot.members.some(m=>m.rut===identifier)) return {local:"manual",reason:"Sin vigencia en la copia. Verifica con el socio."};
  return {local:"manual",reason:"RUT ausente en la copia. Verifica identidad en recepción."};
}
function enqueue(entry) {
  const pending = queue();
  if (pending.length >= 300) throw new Error("La cola alcanzó 300 operaciones. Reconecta para sincronizar.");
  saveLocal(queueKey(), [...pending,entry]);
  navigation();
}
async function sendCommand(path, data) {
  data.command = crypto.randomUUID();
  const identifier = cleanRut(data.rut);
  data.rut = identifier;
  if (!offline && navigator.onLine) {
    try { return await api(path, data); }
    catch (error) {
      if (!error.network) throw error;
      offline = true;
      enqueue({path,data,error:"",uncertain:true});
      return {message:"Respuesta interrumpida. Se conservó la misma operación para reintentar.",provisional:true};
    }
  }
  offline = true;
  if (!hasLease()) throw new Error("La copia de contingencia venció o falta. Usa el registro manual hasta reconectar.");
  const decision = path === "access" ? offlineAccess(identifier) : null;
  if (path === "sale" && !snapshot.members.some(m=>m.rut===identifier)) throw new Error("Socio ausente en la copia de contingencia.");
  Object.assign(data, {lease:snapshot.lease,capturado:Math.floor(Date.now()/1000)});
  if (decision) data.local = decision.local;
  enqueue({path,data,error:""});
  return {message:"Operación local guardada. Pendiente de conciliación.",provisional:true,...decision};
}
async function syncQueue() {
  if (syncing || !user || offline) return;
  syncing = true;
  const key = queueKey();
  let sent = 0;
  try {
    for (const item of queue()) {
      if (item.error) break;
      try {
        await api(item.path,item.data);
        saveLocal(key,readLocal(key,[]).filter(x=>x.data.command!==item.data.command));
        sent++;
      } catch (error) {
        if (error.network) { offline = true; break; }
        if (error.status===401) throw error;
        saveLocal(key,readLocal(key,[]).map(x=>x.data.command===item.data.command ? {...x,error:error.message} : x));
        break;
      }
    }
  } finally { syncing = false; navigation(); }
  return sent;
}
function loginPage() {
  return hero("FitLife Studio","Inicio de sesión","Ingresa con tu cuenta interna de Administración, Recepción o Entrenador.") +
    `<section class="card login"><h2>Acceso al sistema</h2>${form("login",
      field("usuario","Usuario","","text",'required autocomplete="username" maxlength="40"')+
      field("clave","Contraseña","","password",'required autocomplete="current-password" maxlength="128"'),"Ingresar","primary")}
      <p class="small">Entorno académico. Utiliza exclusivamente datos de demostración.</p></section>`;
}
async function accessPage() {
  const entries = offline ? [] : await api("access");
  const result = latestAccess;
  const state = !result ? "" : result.provisional ? "warn" : result.allowed ? "good" : "bad";
  const title = !result ? "Listo para validar" : result.provisional ? "Registro provisional" : result.allowed ? "Permitido" : "Denegado";
  const output = `<section class="card decision ${state}" aria-live="polite"><div class="eyebrow">Resultado</div><h2>${title}</h2>
    <p>${e(result?.reason ?? "Ingresa el RUT del socio para verificar su vigencia.")}</p>
    ${result?.member ? `<p class="small">${e(result.member.name)} · Vence ${e(result.member.ends)}</p>` : ""}
    ${result?.at ? `<p class="small">${dt(result.at)}</p>` : ""}
    ${!offline && result?.member && result.reason==="Plan vencido" ? go("Renovar plan","cobros/"+result.member.id) : ""}</section>`;
  return hero("01 / Recepción","Control de acceso","Verifica el plan por RUT. Los ingresos autorizados registran la asistencia automáticamente.") +
    `<div class="grid"><section class="card access-card"><h2>Verificar ingreso</h2>${form("access",
       field("rut","RUT del socio","","text",'required placeholder="Ej. 12345678-5" maxlength="14" autocomplete="off"'),"Validar ingreso","primary")}
       <p class="small">${offline ? "Validación provisional con copia local." : "Si no recuerda su RUT, busca por nombre y verifica su identidad."}</p>
       ${offline ? "" : go("Buscar socio","socios")}</section>${output}</div>` +
    `<div class="details">${card("Actividad reciente",displayTable(["Fecha y hora","Socio","Decisión","Motivo","Origen"],
      entries.map(r=>[dt(r.at),e(r.name),badge(r.allowed?"Permitido":"Denegado",r.allowed?"":"bad"),e(r.reason),e(r.source)]),
      offline ? "El historial está disponible al reconectar." : "Todavía no hay validaciones."))}</div>`;
}
function memberFields(member) {
  const editable = !member || user.role==="admin";
  const basic = member || {};
  return hidden("id",basic.id || 0) +
    (member ? field("rut","RUT",basic.rut,"text","readonly") : field("rut","RUT","","text",'required maxlength="14"')) +
    field("nombre","Nombre completo",basic.name || "","text",editable?'required maxlength="100"':"readonly") +
    field("nacimiento","Fecha de nacimiento",basic.born || "","date",editable?"":"readonly") +
    field("direccion","Dirección",basic.address || "","text",'maxlength="140"') +
    field("telefono","Teléfono",basic.phone || "","tel",'required maxlength="25"') +
    field("correo","Correo",basic.email || "","email",'required maxlength="120"') +
    field("emergencia","Contacto de emergencia",basic.emergency || "","text",editable?'required maxlength="100"':"readonly") +
    field("tel_emergencia","Teléfono de emergencia",basic.emergency_phone || "","tel",editable?'required maxlength="25"':"readonly") +
    (member ? "" : check("aviso","Informé el propósito de registro, vigencia y asistencia. El contacto de emergencia se reserva para ese fin."));
}
async function membersPage() {
  if (targetId() && user.role!=="entrenador") return memberPage(await api("member?id="+targetId()));
  const results = await api("members?q="+encodeURIComponent(found));
  const trainer = user.role==="entrenador";
  return hero("02 / Personas","Socios",trainer?"Consulta limitada al nombre, edad y contacto de emergencia.":"Busca una ficha por nombre o RUT. Usa los datos de la ficha para verificar identidad si el socio olvidó su RUT.") +
    card("Buscar socio",form("search",field("q","Nombre o RUT",found,"search",'maxlength="100"'),"Buscar") +
      (trainer ? displayTable(["Nombre","Edad","Contacto de emergencia","Teléfono"],results.map(r=>[e(r.name),e(r.age),e(r.emergency),e(r.emergency_phone)])) :
        displayTable(["Socio","RUT","Ficha"],results.map(r=>[e(r.name),e(r.rut),go("Abrir ficha","socios/"+r.id)])))) +
    (trainer ? "" : card("Registrar socio",detail("Abrir formulario de alta",form("member",memberFields(null),"Guardar ficha","primary"))));
}
function memberPage(data) {
  const {member,memberships,payments} = data;
  const valid = memberships.filter(m=>m.state==="Activa" || m.state==="Congelada");
  const freezeOptions = valid.map(m=>opt(m.id,m.plan_name+" · "+m.ends)).join("");
  return hero("02 / Ficha de socio",member.name,member.rut) +
    `<div class="actions no-print">${go("Volver a socios","socios")}${go("Registrar cobro","cobros/"+member.id)}</div><br>` +
    card("Membresías",displayTable(["Plan","Inicio","Vencimiento","Estado"],
      memberships.map(m=>[e(m.plan_name),e(m.starts),e(m.ends),badge(m.state,m.state==="Activa"?"":"warn")])) +
      (valid.length ? detail("Solicitar congelamiento",form("freeze",
        select("membresia","Membresía",freezeOptions)+field("inicio","Inicio",dateNow(),"date")+
        field("dias","Días","","number",'required min="1" max="90"')+
        select("causa","Motivo general",opt("certificado verificado","Certificado verificado fuera del sistema")+opt("viaje","Viaje superior a 15 días"))+
        '<label class="small"><input type="checkbox" name="viaje_verificado" value="si">Si el motivo es viaje, verifiqué que dura más de 15 días.</label>',
        "Solicitar congelamiento")) : "")) +
    card("Historial de pagos",displayTable(["Folio","Plan","Medio","Monto original","Neto","Estado","Detalle"],
      payments.map(p=>["#"+p.id,e(p.plan_name),e(p.method),money(p.amount),money(p.net),
        badge(p.void?"Anulado":p.confirmed?"Confirmado":"Pendiente",p.confirmed&&!p.void?"":"warn"),
        p.confirmed?go("Comprobante","comprobante/"+p.id):"—"])) +
      payments.map(p=>detail("Acciones para pago #"+p.id,
        (!p.confirmed&&!p.void?form("confirm",hidden("pago",p.id)+check("verificado","Verifiqué el abono en la cuenta del gimnasio."),"Confirmar transferencia","primary"):"")+
        form("note",hidden("pago",p.id)+field("motivo","Observación","","text",'required minlength="4" maxlength="160"'),"Guardar observación","secondary")+
        (user.role==="admin"&&!p.void?form("adjust",hidden("pago",p.id)+field("monto",p.confirmed?"Monto a compensar (CLP)":"Monto 0 para cancelar",p.confirmed?"":0,"number",'required min="0"')+
          field("motivo","Motivo de la corrección","","text",'required minlength="4" maxlength="160"'),"Registrar corrección"):""))).join("")) +
    card("Datos del socio",detail("Editar ficha",form("member",memberFields(member),"Guardar cambios","primary")));
}
async function salesPage() {
  const data = offline ? {plans:snapshot?.plans || [],deals:[]} : await api("catalog");
  const active = data.plans.filter(p=>p.active!==0);
  const member = !offline && targetId() ? (await api("member?id="+targetId())).member : null;
  return hero("03 / Recepción","Membresías y cobros","Inscripción, renovación y pases diarios. El servidor calcula la vigencia y verifica el monto.") +
    card(offline ? "Registrar en contingencia" : "Registrar pago",
      active.length ? form("sale",field("rut","RUT del socio",member?.rut || "","text",'required maxlength="14"') +
        select("plan","Plan",active.map(p=>`<option value="${p.id}" data-price="${p.price}" data-kind="${e(p.kind)}">${e(p.name)} · ${money(p.price)}</option>`).join("")) +
        select("convenio","Convenio",opt(0,"Sin convenio")+data.deals.filter(d=>d.active&&d.starts<=dateNow()&&d.ends>=dateNow()).map(d=>
          `<option value="${d.id}" data-pct="${d.pct}">${e(d.name)} · ${d.pct}%</option>`).join("")) +
        select("medio","Medio de pago",opt("efectivo","Efectivo")+opt("tarjeta","Tarjeta / POS")+opt("transferencia","Transferencia")) +
        field("referencia","Referencia POS o bancaria","","text",'maxlength="40" autocomplete="off"')+
        field("cotizacion","Total a registrar (CLP)","","number","readonly") +
        `<p class="small">${offline?"El importe se valida al reconectar. Guarda el respaldo externo para conciliar.":"Transferencia requiere verificación manual del abono. Usa la referencia externa, sin datos de tarjeta."}</p>`,
        "Registrar cobro","primary") : `<p>No hay catálogo local. Conéctate para actualizarlo.</p>`);
}
function quote(sale) {
  const plan = sale.elements.plan.selectedOptions[0];
  const deals = sale.elements.convenio;
  deals.disabled = plan.dataset.kind!=="mensual";
  if (deals.disabled) deals.value = "0";
  sale.elements.cotizacion.value = Math.floor((Number(plan.dataset.price)*(100-Number(deals.selectedOptions[0]?.dataset.pct || 0))+50)/100);
  sale.elements.referencia.required = sale.elements.medio.value!=="efectivo";
  sale.elements.referencia.closest("label").hidden = !sale.elements.referencia.required;
}

async function pausesPage() {
  const records = await api("freezes");
  return hero("04 / Vigencia","Congelamientos","Recepción solicita desde la ficha. Administración revisa el motivo y decide.") +
    card("Solicitudes",displayTable(["Socio","Plan","Período","Motivo","Estado","Decisión"],records.map(r=>[
      e(r.name),e(r.plan_name),e(r.starts)+" a "+e(r.ends),e(r.reason),badge(r.decision,r.decision==="pendiente"?"warn":""),
      user.role==="admin"&&r.decision==="pendiente" ? detail("Resolver #"+r.id,form("decide",hidden("solicitud",r.id)+
        select("decision","Decisión",opt("aprobada","Aprobar")+opt("rechazada","Rechazar"))+
        field("motivo","Motivo de decisión","","text",'required minlength="4" maxlength="160"'),"Confirmar decisión")) : e(r.decided_reason)
    ])));
}
const onOff = value => select("activo","Estado",opt(1,"Activo",value)+opt(0,"Inactivo",value));
function planEditor(plan={}) {
  return form("plan",hidden("id",plan.id||0)+field("nombre","Nombre",plan.name||"")+
    select("tipo","Tipo",opt("mensual","Mensual",plan.kind)+opt("periodico","Trimestral / semestral / anual",plan.kind)+opt("diario","Pase diario",plan.kind))+
    field("dias","Duración en días",plan.days??30,"number",'required min="1" max="366"')+
    field("precio","Tarifa (CLP)",plan.price??"","number",'required min="100"')+
    field("tope","Tope de congelamiento",plan.freeze_days??0,"number",'required min="0" max="90"')+onOff(plan.active??1)+
    field("motivo","Motivo del cambio","","text",'required minlength="4" maxlength="160"'),"Guardar plan");
}
function dealEditor(deal={}) {
  return form("deal",hidden("id",deal.id||0)+field("nombre","Nombre",deal.name||"")+
    select("porcentaje","Descuento",opt(15,"15%",deal.pct)+opt(20,"20%",deal.pct))+
    field("inicio","Desde",deal.starts||dateNow(),"date")+field("fin","Hasta",deal.ends||dateNow(),"date")+
    onOff(deal.active??1),"Guardar convenio");
}
async function catalogPage() {
  const data = await api("catalog");
  return hero("06 / Administración","Planes y convenios","Las tarifas se aplican a ventas nuevas. Los convenios se limitan al plan mensual y a fechas vigentes.") +
    `<div class="grid">${card("Planes",detail("Crear plan",planEditor())+data.plans.map(p=>detail(p.name+" · "+money(p.price)+(p.active?"":" · inactivo"),planEditor(p))).join(""))}
    ${card("Convenios",detail("Crear convenio",dealEditor())+data.deals.map(d=>detail(d.name+" · "+d.pct+"%",dealEditor(d))).join(""))}</div>`;
}
let cashFilters = null;
async function cashPage() {
  const cashiers = await api("cashiers");
  const admin = user.role==="admin";
  cashFilters ||= {fecha:dateNow(),turno:user.shift,cajero:String(user.id)};
  const q = cashFilters;
  const info = await api("cash?"+new URLSearchParams(q));
  const filters = form("cash-query",field("fecha","Fecha",q.fecha,"date")+
    select("turno","Turno",opt("AM","AM · antes de las 12",q.turno)+opt("PM","PM · desde las 12",q.turno)+(admin?opt("Día","Día completo",q.turno):""))+
    select("cajero","Cajero",(admin?opt(0,"Todos (consolidado diario)",q.cajero):"")+cashiers.map(u=>opt(u.id,u.name,q.cajero)).join("")),"Consultar");
  const kpis = `<div class="kpis">${Object.entries({...info.methods,"neto":info.net}).map(([key,val])=>`<div class="kpi"><span class="small">${e(key)}</span><strong>${money(val)}</strong></div>`).join("")}</div>`;
  const state = info.closed ? `<p>${badge("Cierre #"+info.folio)} · Efectivo contado ${money(info.counted)} · Diferencia ${money(info.difference)}</p><p class="small">${e(info.reason || "")}</p>` :
    info.locked ? `<p>${badge("Día consolidado","warn")}</p>` :
      detail("Confirmar cierre",form("cash",Object.entries(q).map(([key,val])=>hidden(key,val)).join("")+
        field("contado","Efectivo contado (CLP)",Math.max(0,info.methods.efectivo),"number",'required min="0"')+
        field("motivo","Nota obligatoria si existe diferencia","","text",'maxlength="160"')+
        check("sin_cola","Concilié los registros pendientes de contingencia antes de cerrar."),"Cerrar y bloquear","primary"));
  return hero("05 / Finanzas","Caja","Consulta los movimientos por turno o día. El cierre guarda un corte inmutable y separa transferencias pendientes.") +
    `<div class="grid">${card("Seleccionar período",filters)}${card("Resumen de caja",kpis+displayTable(["Concepto","Neto"],Object.entries(info.concepts).map(([key,val])=>[e(key),money(val)]))+state)}</div><br>` +
    card("Movimientos",displayTable(["Fecha y hora","Folio","Cajero","Concepto","Tipo","Medio","Monto"],
      info.moves.map(m=>[dt(m.at),"#"+m.payment_id,e(m.operator),e(m.concept),e(m.movement),e(m.method),money(m.amount)])))+
    card("Transferencias pendientes",displayTable(["Folio","Solicitud","Monto pendiente"],
      info.pending.map(p=>["#"+p.id,dt(p.at),money(p.amount)])));
}
async function reportsPage() {
  const data = await api("reports");
  const deadlines = entries=>displayTable(["Socio","RUT","Vence"],entries.map(r=>[go(r.name,"socios/"+r.id),e(r.rut),e(r.due)]));
  const rate = data.cohort ? (100*data.renewed/data.cohort).toLocaleString("es-CL",{maximumFractionDigits:1})+"%" : "Sin cohorte";
  return hero("07 / Administración","Informes","Seguimiento semanal de vigencias, comparación de ingresos y conteo mensual de entradas.")+
    `<div class="grid">${card("Vencidos últimos 7 días",deadlines(data.expired))}${card("Vencen entre 3 y 7 días",deadlines(data.upcoming))}
    ${card("Renovación sin interrupción","<p>"+e(rate)+" · "+data.renewed+" de "+data.cohort+" planes que vencieron en los últimos 7 días.</p>")}</div><br>` +
    card("Ingresos por mes, medio y concepto",displayTable(["Mes","Medio","Concepto","Neto"],data.income.map(r=>[e(r.month),e(r.method),e(r.concept),money(r.amount)])))+
    card("Entradas registradas por hora",displayTable(["Día","Hora","Entradas"],data.attendance.map(r=>[e(r.day),e(r.hour)+":00",e(r.count)]))+
      '<p class="small">Es un conteo de entradas. El gimnasio no registra salidas ni calcula ocupación instantánea.</p>');
}
function accountEditor(account={}) {
  return form("user",hidden("id",account.id||0)+
    field("usuario","Usuario",account.name||"","text",account.id?"readonly":'required maxlength="40" autocomplete="off"')+
    select("rol","Rol",Object.entries(roleLabels).map(([key,label])=>opt(key,label,account.role??"recepcion")).join(""))+
    field("clave",account.id?"Nueva contraseña (opcional)":"Contraseña inicial","","password",(account.id?"": "required ")+'minlength="12" maxlength="128" autocomplete="new-password"')+
    onOff(account.active??1),"Guardar cuenta");
}
async function teamPage() {
  const [users,audit] = await Promise.all([api("users"),api("audit")]);
  return hero("08 / Administración","Usuarios y auditoría","Asigna cuentas individuales. El historial de acciones críticas queda disponible para revisión.")+
    card("Usuarios internos",detail("Crear cuenta",accountEditor())+users.map(u=>detail(u.name+" · "+roleLabels[u.role]+(u.active?"":" · inactivo"),accountEditor(u))).join(""))+
    card("Bitácora reciente",displayTable(["Fecha y hora","Usuario","Acción","Entidad","ID","Detalle","Origen"],audit.map(a=>
      [dt(a.at),e(a.operator),e(a.event),e(a.entity),e(a.target),e(a.detail||"—"),e(a.origin)])));
}
async function receiptPage() {
  const {payment:p,notes,adjustments} = await api("receipt?id="+targetId());
  return `<div class="print-receipt">${hero("FitLife Studio","Comprobante interno #"+p.id,"Registro de pago. Documento interno sin validez tributaria.")}
    ${card(p.name,displayTable(["Plan","Confirmación","Operador","Medio","Monto original"],[[e(p.plan_name),dt(p.confirmed),e(p.operator),e(p.method),money(p.amount)]])+
      (p.reference?"<p>Referencia "+e(p.reference)+"</p>":""))}
    ${card("Ajustes",displayTable(["Fecha","Motivo","Monto"],adjustments.map(a=>[dt(a.at),e(a.reason),money(a.amount)])))}
    ${card("Observaciones",displayTable(["Fecha","Nota"],notes.map(n=>[dt(n.at),e(n.reason)])))}
    <div class="actions no-print"><button type="button" data-click="print">Imprimir comprobante</button>${go("Volver a socios","socios")}</div></div>`;
}
function offlinePage() {
  const items = queue();
  const description = hasLease() ? "Copia válida hasta "+new Date(snapshot.expires*1000).toLocaleTimeString("es-CL") : "Sin copia válida para nuevas operaciones";
  return hero("09 / Continuidad","Contingencia","La cola conserva cada ingreso y cobro en este navegador. La sincronización requiere la misma cuenta activa.")+
    card("Estado del puesto",`<div class="kpis"><div class="kpi"><span class="small">En cola</span><strong>${items.length}</strong></div></div>
      <p>${e(description)}.</p><p class="small">Los resultados provisionales se revisan al reconectar. Concilia con Administración los cobros en conflicto.</p>
      <div class="actions"><button type="button" data-click="sync">Reconectar y sincronizar</button></div>`)+
    card("Operaciones locales",displayTable(["Operación","RUT","Monto","Estado","Acciones"],items.map(item=>[
      e(item.path==="sale"?"Cobro":"Acceso"),e(item.data.rut),item.data.cotizacion?money(item.data.cotizacion):"—",
      e(item.error|| (item.uncertain?"Respuesta interrumpida":"Pendiente de envío")),
      item.error ? `<div class="actions"><button type="button" data-click="retry" data-id="${e(item.data.command)}">Reintentar</button>
        <button class="secondary" type="button" data-click="discard" data-id="${e(item.data.command)}">Retirar tras conciliar</button></div>`:"—"
    ])));
}
const screens = {acceso:accessPage,socios:membersPage,cobros:salesPage,pausas:pausesPage,catalogo:catalogPage,caja:cashPage,
  informes:reportsPage,equipo:teamPage,contingencia:offlinePage,comprobante:receiptPage};
async function render() {
  const version = ++viewNumber;
  const path = pageId();
  navigation();
  try {
    let html;
    if (!user) html = loginPage();
    else if (user.role==="entrenador" && path!=="socios") html = await membersPage();
    else if (offline && !["acceso","cobros","contingencia"].includes(path)) html = offlinePage();
    else if (!Object.hasOwn(screens,path) || (nav.find(([id])=>id===path)?.[2] && user.role!=="admin")) html = await accessPage();
    else html = await screens[path]();
    if (version !== viewNumber) return;
    $("screen").innerHTML = html;
    const sale = document.querySelector('form[data-action="sale"]');
    if (sale) quote(sale);
  } catch (error) {
    if (version !== viewNumber) return;
    if (error.network && user && !offline) {
      offline = true;
      return render();
    }
    if (error.status===401) {
      await refreshSession();
      return render();
    }
    $("screen").innerHTML = hero("FitLife Studio","No se pudo abrir este módulo",error.message);
  }
}
async function reconnect() {
  await refreshSession();
  if (offline) { notify("El servidor sigue sin responder.",true); return; }
  if (!user) { notify("Inicia sesión para recuperar tu cola.",true); return; }
  const sent = await syncQueue();
  await prepareOffline();
  notify(sent ? sent+" operaciones sincronizadas." : queue().length ? "Revisa los conflictos pendientes." : "Conexión restablecida. La cola está vacía.");
}
document.addEventListener("change", event => {
  const sale = event.target.closest('form[data-action="sale"]');
  if (sale) quote(sale);
  const cash = event.target.closest('form[data-action="cash-query"]');
  if (cash && event.target.name==="turno" && user.role==="admin") {
    cash.elements.cajero.value = cash.elements.turno.value==="Día" ? "0" : String(user.id);
  }
});
document.addEventListener("submit", async event => {
  const f = event.target;
  const action = f.dataset.action;
  if (!action) return;
  event.preventDefault();
  if (busy) return;
  busy = true;
  const submit = f.querySelector('button[type="submit"]');
  submit.disabled = true;
  let redraw = false;
  try {
    const data = Object.fromEntries(new FormData(f));
    if (action==="login") {
      await api("login",data);
      await refreshSession();
      if (!offline && user) { await syncQueue(); await prepareOffline(); }
      cashFilters = null;
      latestAccess = null;
      found = "";
      redraw = true;
    } else if (action==="search") {
      found = data.q;
      redraw = true;
    } else if (action==="cash-query") {
      const next = await api("cash?"+new URLSearchParams(data));
      if (next) cashFilters = data;
      redraw = true;
    } else {
      if (action==="sale") data.convenio ||= "0";
      if (action==="cash" && queue().length) throw new Error("Sincroniza o concilia la cola de este puesto antes de cerrar.");
      const result = ["sale","access"].includes(action) ? await sendCommand(action,data) : await api(action,data);
      if (action==="access") latestAccess = result;
      notify(result.message, action==="access" && result.allowed===false);
      redraw = true;
      if (!offline && (["member","sale","confirm","decide","adjust","plan"].includes(action) ||
          action==="access" && (!snapshot || snapshot.expires < Date.now()/1000+600))) {
        try { await prepareOffline(); }
        catch { notify(result.message + " No se pudo actualizar la copia de contingencia.",true); }
      }
      if (action==="member" && data.id==="0") location.hash = "socios/"+result.id;
    }
  } catch (error) {
    notify(error.message,true);
    if (error.status===401) {
      await refreshSession();
      redraw = true;
    }
  } finally {
    busy = false;
    submit.disabled = false;
    if (redraw) await render();
    if (action==="access" && redraw) $("screen").querySelector('input[name="rut"]')?.focus();
  }
});
document.addEventListener("click", async event => {
  const button = event.target.closest("[data-click]");
  if (!button || busy) return;
  if (button.dataset.click==="print") { window.print(); return; }
  busy = true;
  try {
    const id = button.dataset.id;
    if (button.dataset.click==="discard") {
      if (!confirm("Retira el registro local solo tras conciliarlo. ¿Confirmas?")) return;
      saveLocal(queueKey(),queue().filter(x=>x.data.command!==id));
      notify("Registro retirado de este navegador.");
    } else {
      if (button.dataset.click==="retry") saveLocal(queueKey(),queue().map(x=>x.data.command===id?{...x,error:""}:x));
      await reconnect();
    }
  } catch (error) { notify(error.message,true); }
  finally { busy = false; await render(); }
});
$("logout").addEventListener("click", async () => {
  if (busy || !user) return;
  if (queue().length || offline) { notify("Reconecta y concilia tu cola antes de cerrar sesión.",true); return; }
  try {
    await api("logout",{ok:true});
    localStorage.removeItem(storageKey("snapshot-"+user.id));
    localStorage.removeItem(storageKey("identity"));
    user = snapshot = latestAccess = null;
    notify("");
    await render();
  } catch (error) { notify(error.message,true); }
});
window.addEventListener("hashchange", () => { if (!busy) render(); });
window.addEventListener("offline", () => { offline = true; if (!busy) render(); });
window.addEventListener("online", async () => {
  if (busy) return;
  try { await reconnect(); } catch (error) { notify(error.message,true); }
  await render();
});
(async () => {
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {
    notify("El navegador no habilitó la recarga sin conexión.",true);
  });
  try {
    await refreshSession();
    if (user && !offline && user.role!=="entrenador") await prepareOffline();
  } catch (error) { notify(error.message,true); }
  await render();
})();
