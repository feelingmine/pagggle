// Synthetic, in-memory API fixture for offline browser checks. Never loaded by the app.
(() => {
  const timestamp="2026-10-08T06:20:00Z";
  const source={id:"source-demo",title:"产品定制页（合成测试）",kind:"page",status:"read",body:"We manufacture silicone gaskets for industrial equipment. We manufacture silicone gaskets to customer drawings. Certification scope and public case studies have not been verified.",url:"https://example.com/custom",updated_at:timestamp};
  const citation=quote=>[{source_id:source.id,quote}];
  const facts=[
    {category:"product",statement:"生产用于工业设备的硅胶密封垫。",scope:"产品用于工业设备密封。具体材料、尺寸与工况边界需结合实际规格核对。",status:"confirmed",citations:citation("We manufacture silicone gaskets for industrial equipment.")},
    {category:"business",statement:"可按客户图纸制作，是否接受样品仍需确认。",scope:"来源明确提及按图纸制作；接受样品的接单范围尚不明确。",status:"pending",citations:citation("We manufacture silicone gaskets to customer drawings.")},
    {category:"capability",statement:"认证及相关能力的适用范围尚未核实。",scope:"不能将未经核实的认证作为产品能力承诺；需由企业补充适用产品范围。",status:"pending",citations:citation("Certification scope and public case studies have not been verified.")},
    {category:"case",statement:"可披露案例与对应产品关系需要补充。",scope:"资料未覆盖案例不代表企业没有案例。",status:"pending",citations:citation("Certification scope and public case studies have not been verified.")}
  ];
  const project={id:"demo",name:"演示项目",site_url:"https://example.com/"};
  const db={project,sources:[source],profiles:[{id:"profile-demo",version:1,status:"draft",reason:"合成测试资料，不代表真实分析",updated_at:timestamp,payload:{facts,gaps:[]}}],jobs:[],model_runs:[]};
  let demands=[], imported=new Map();
  window.fetch=async (url,options={})=>{
    const path=String(url).replace(/^\/api/,""),data=options.body?JSON.parse(options.body):null;
    let value,status=200;
    if(path==="/projects")value=[project];
    else if(path==="/projects/demo")value=db;
    else if(path==="/projects/demo/profiles/review"){
      const old=db.profiles[0];if(data.expected_version!==old.version){status=409;value={detail:"画像已有新版本"};}
      else{const p=structuredClone(old);p.version++;p.status="draft";p.reason=data.reason;const fact=p.payload.facts[data.fact_index];fact.status=data.decision;if(data.statement)fact.statement=data.statement;if(data.scope)fact.scope=data.scope;db.profiles.unshift(p);value=p;}
    }else if(path==="/projects/demo/profiles/confirm"){db.profiles[0].status="confirmed";value=db.profiles[0];}
    else if(path==="/projects/demo/demands")value=demands;
    else if(path==="/projects/demo/intents")value=[];
    else if(path==="/projects/demo/clusters")value={runs:[],settings:{}};
    else if(path.endsWith("/demands/preview")||path.endsWith("/demands/import")){
      const seen=new Set(demands.filter(r=>r.status==="pending").map(r=>r.original.toLowerCase()));
      const lines=data.kind==="keyword"?data.text.split("\n").filter(Boolean):[data.text];
      const records=lines.map((text,i)=>{const duplicate=seen.has(text.toLowerCase());seen.add(text.toLowerCase());return{id:"demand-"+i,original:text,kind:data.kind,status:duplicate?"duplicate":"pending",volume:null,kd:null};});
      value={summary:{input:records.length,valid:records.filter(r=>r.status==="pending").length,duplicate:records.filter(r=>r.status==="duplicate").length,failed:0},records};
      if(path.endsWith("/import")){if(imported.has(data.request_id))value=imported.get(data.request_id);else{demands.push(...records);imported.set(data.request_id,value);}}
    }else if(path.endsWith("/sources")){const s={...data,id:"manual-"+db.sources.length,kind:"manual",status:"read",updated_at:timestamp};db.sources.push(s);value=s;}
    else{status=400;value={detail:"此离线测试未模拟该接口"};}
    return{ok:status>=200&&status<300,status,json:async()=>structuredClone(value)};
  };
})();
