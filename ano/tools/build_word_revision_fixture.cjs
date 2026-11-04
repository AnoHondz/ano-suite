// Public synthetic QA fixture, built with the Word skill's document library.
const {Document,Packer,Paragraph,TextRun,Table,TableRow,TableCell,Header,Footer,
  HeadingLevel,WidthType,TableLayoutType,ShadingType} = require(process.argv[2]);
const fs=require('fs');
const cell=(text,header=false)=>new TableCell({width:{size:4896,type:WidthType.DXA},
  shading:header?{type:ShadingType.CLEAR,fill:'E7EFEB'}:undefined,
  children:[new Paragraph({children:[new TextRun({text,bold:header})]})]});
const doc=new Document({creator:'Ano validation',title:'Public revision fixture',
  styles:{default:{document:{run:{font:'Calibri',size:22}}}},sections:[{
    properties:{page:{size:{width:12240,height:15840},margin:{top:1152,bottom:1152,left:1224,right:1224}}},
    headers:{default:new Header({children:[new Paragraph('PUBLIC SYNTHETIC FIXTURE | ANO')]})},
    footers:{default:new Footer({children:[new Paragraph('Source preserved; no personal records.')]})},
    children:[new Paragraph({text:'Revision validation',heading:HeadingLevel.HEADING_1}),
      new Paragraph({children:[new TextRun('Hello '),new TextRun({text:'Ra',bold:true}),
        new TextRun({text:'vi',italics:true}),new TextRun(', exact body remains.')]}),
      new Paragraph('The table below is editable. The original document must remain unchanged.'),
      new Table({width:{size:9792,type:WidthType.DXA},columnWidths:[4896,4896],layout:TableLayoutType.FIXED,
        rows:[new TableRow({children:[cell('Stage',true),cell('Status',true)]}),
              new TableRow({children:[cell('Review'),cell('Pending')]})]})]
  }]});
Packer.toBuffer(doc).then(buffer=>fs.writeFileSync(process.argv[3],buffer,{flag:'wx'}));
