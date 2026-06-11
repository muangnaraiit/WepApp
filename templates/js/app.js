document.addEventListener("mousemove", function(e){

document.querySelectorAll(".floating").forEach((el,index)=>{

let speed = (index + 1) * 0.01;

el.style.transform =
`translate(${e.clientX * speed}px,
${e.clientY * speed}px)`;

});

});