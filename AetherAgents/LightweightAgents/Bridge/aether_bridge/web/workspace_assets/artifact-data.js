/* Owner: artifact type labels and workspace/private Chat artifact reads.
 * Must never: edit artifacts, render pages, expose another operator’s Chat, or write agent memory.
 */
import {api, store} from './kit.js';
const kinds={summary:'Summary',bullets:'Bullet Points',tasks:'Task List',aether:'To Aether',teachframe:'Teach Frame',note:'Note',html:'HTML Page',svg:'SVG'};
async function libraryData(){const chat=await api('/api/model-chat');return [...store.artifacts.map(a=>({...a,origin:'workspace'})),...chat.chats.filter(c=>!c.deleted).flatMap(c=>(c.artifacts||[]).map(a=>({...a,origin:'chat',chat:c.id,source:c.title,archived:false,tags:c.tags||[],updated_at:a.at})))];}
const teachTemplate='⟪TEACHFRAME_TOPIC_NAME_V1⟫\nSCHOOL: Unspecified\nAUDIENCE: Unspecified\nPREREQUISITES: Unspecified\n\n[ TITLE ]\n\n[ PURPOSE ]\n\n[ DEFINITIONS ]\n\n[ WORKFLOW ]\n\n[ EXAMPLES ]\n\n[ LIMITATIONS & OPEN QUESTIONS ]\n\n[ CROSS_REFS ]\n';
async function fullArtifact(a){if(a.origin!=='chat')return a;const chat=await api('/api/model-chat?id='+encodeURIComponent(a.chat));const saved=chat.artifacts.find(x=>x.id===a.id);if(!saved)throw Error('This artifact was not found in its source chat.');return {...a,...saved};}

export {fullArtifact, kinds, libraryData, teachTemplate};
