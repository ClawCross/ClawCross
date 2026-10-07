// Small catalog used to verify selection/payload semantics, without live Agents.
const tools=[{name:'read_file',category:'files'}, {name:'send_to_group',category:'groups'},
  {name:'manage_team',category:'groups',opt_in:true}];
const templates=[
  {id:'chat',label:{zh:'纯聊天',en:'Chat only'},description:{zh:'文字交流，无工具',en:'No tools'},tools:[]},
  {id:'group',label:{zh:'群聊伙伴',en:'Group companion'},description:{zh:'严格隔离',en:'Strict isolation'},tools:['send_to_group']},
  {id:'personal',label:{zh:'私人助手',en:'Personal assistant'},description:{zh:'AI 审核',en:'AI review'},tools:['read_file','send_to_group']},
  {id:'admin',label:{zh:'管理员',en:'Administrator'},description:{zh:'人工审核',en:'Human review'},tools:tools.map(tool=>tool.name)},
];
module.exports={tools,templates};
