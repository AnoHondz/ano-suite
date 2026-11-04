// Build-time only. Runtime decks fill this template with Python's XML/ZIP tools.
process.env.NODE_PATH = require('path').dirname(process.argv[2]);
require('module').Module._initPaths();
const PptxGenJS = require(process.argv[2]);
const {applyTheme} = require(process.argv[3]);
const pres = new PptxGenJS();
const THEME = {name:'Ano Forest', headFontFace:'Cambria', bodyFontFace:'Calibri', colors:{
  dk1:'183B35',lt1:'FFFFFF',dk2:'355650',lt2:'F3F6F3',accent1:'4B8173',accent2:'CC9D55',
  accent3:'83A398',accent4:'D6E4DD',accent5:'8B6D47',accent6:'5A7770',hlink:'286C8E',folHlink:'755C86'}};
pres.layout='LAYOUT_WIDE';
pres.author='Ano'; pres.subject='Local supplied-content template'; pres.title='Ano supplied-content template';
pres.theme={headFontFace:THEME.headFontFace,bodyFontFace:THEME.bodyFontFace};
const C=pres.SchemeColor;
function frame(name,dark){
  pres.defineSlideMaster({title:name,background:{color:dark?C.text1:C.background2},objects:[
    {placeholder:{options:{name:'title',type:'title',x:.65,y:.55,w:11.95,h:.95,fontSize:34,bold:true,color:dark?C.background1:C.text1,margin:0}}},
    {text:{text:'Supplied content · Ano',options:{x:.65,y:7,w:9,h:.2,fontSize:10,color:dark?C.accent4:C.text2,margin:0,isTextBox:true}}}
  ],slideNumber:{x:12,y:7,w:.65,h:.2,fontSize:10,color:dark?C.accent4:C.text2,margin:0}});
}
frame('Cover',true); frame('Rows',false); frame('Cards',false);
pres.addSection({title:'Introduction'});
let slide=pres.addSlide({masterName:'Cover',sectionTitle:'Introduction'});
slide.addText('{{DECK_TITLE}}',{placeholder:'title'});
slide.addText('{{DECK_SUBTITLE}}',{x:.7,y:2.25,w:9.5,h:1.8,fontSize:25,color:C.accent4,margin:0,isTextBox:true,objectName:'DeckSubtitle'});
slide.addShape(pres.ShapeType.ellipse,{x:10.5,y:3.7,w:1.6,h:1.6,fill:{color:C.accent1},line:{color:C.accent1},objectName:'CoverMotif'});
pres.addSection({title:'Content'});
slide=pres.addSlide({masterName:'Rows',sectionTitle:'Content'});
slide.addText('{{TITLE}}',{placeholder:'title'});
for(let i=0;i<4;i++){
  slide.addShape(pres.ShapeType.ellipse,{x:.75,y:1.85+i*1.15,w:.55,h:.55,fill:{color:C.accent1},line:{color:C.accent1},objectName:'StepCircle'+i});
  slide.addText(String(i+1),{x:.75,y:1.94+i*1.15,w:.55,h:.3,fontSize:17,bold:true,align:'center',color:C.background1,margin:0,isTextBox:true,objectName:'StepNumber'+i});
  slide.addText('{{ITEM'+(i+1)+'}}',{x:1.6,y:1.82+i*1.15,w:10.6,h:.9,fontSize:21,color:C.text1,margin:0,isTextBox:true,objectName:'Item'+i});
}
slide=pres.addSlide({masterName:'Cards',sectionTitle:'Content'});
slide.addText('{{TITLE}}',{placeholder:'title'});
for(let i=0;i<4;i++){
  const x=.7+(i%2)*6.1,y=1.8+Math.floor(i/2)*2.35;
  slide.addShape(pres.ShapeType.roundRect,{x,y,w:5.8,h:2.1,rectRadius:.12,fill:{color:C.background1},line:{color:C.accent4},objectName:'ContentCard'+i});
  slide.addText('{{ITEM'+(i+1)+'}}',{x:x+.3,y:y+.35,w:5.2,h:1.4,fontSize:21,color:C.text1,margin:0,isTextBox:true,objectName:'Item'+i});
}
(async()=>{await pres.writeFile({fileName:process.argv[4]});await applyTheme(process.argv[4],THEME);})();
