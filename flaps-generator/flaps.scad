// Flap Generator for splitflap design based on: https://github.com/adamgmakes/SplitFlapDisplay
//
// Flap generator created by Richard Garsthagen (the.anykey@gmail.com)
// License under creative commons: https://creativecommons.org/licenses/by-nc-sa/4.0/

$fn=180; // Quality of render
layers = 3;
layerheight = 0.16;
fontsize = 28;

// USE [F6] to render the flaps

// Make the flaps 
//MakeFlaps(99);

// Make the letter inlays - select the color layer
//MakeFlaps(0);

// Show Preview of all the flaps - NOT FOR PRINTING
PreviewFlaps();


// Fonts to use
fonts = ["Consolas:style=bold", "Arial:style=Narrow Bold"];
charFont = [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0];

// 64 Characters you want to use
chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789?!@#$&()-+=;:%'\u20AC\"\u2191\u2193\u20BF\u00b0\u263A_.♥[] ";

// Color layer, to generate as individual colors
colorLayer = [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,3,1,1,2,0,1,1,0,0,0,0,0,0,0,2,0,2,1,3,0,3,0,0,1,3,3,0];

// Per Character Font Size overwrite
charSizeOffset = [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,-4,0,0,0,0,0,0,0,0,0,0,0,0,0,0,5,5,0,0,0,0,0,0,0,0,0];

// Per Character Y Position overwrite -> default is centered
charYposOffset = [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,-3.5,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,9,0,9,0,0,0,9,1.5,-12,-12,0,0,0,0];

colors = ["white", "red", "green", "yellow"];

module PreviewFlaps(){
    for ( y = [0 : 7] ){
        for ( x = [0 : 7] ){
            char = (y*8)+x;
            translate([34+(x*34),22+(y*43),0])
            flapPreview(char); 
        }
    }
    
}

module MakeFlaps(part){
    for ( y = [0 : 5] ){
        for ( x = [0 : 2 : 12] ){
            char = (y*14)+x;
            if (char<64) {
                if (char==0){
                 translate([17+(x*17),22+(y*43),0])
                 flap(63, char, char+1, part); }
                else if (char==63) {
                 translate([17+(x*17),22+(y*43),0])
                 flap(char-1, char, 0, part); }
                else {
                 translate([17+(x*17),22+(y*43),0])
                 flap(char-1, char, char+1, part);} 
            }
        }
    }
}

module flapPreview(c1){
     difference(){ 
         union(){
         color("black")
         linear_extrude(h=(layers*layerheight))
         import("flap.dxf");
         
         color("black")
         linear_extrude(h=(layers*layerheight))
         rotate([0,0,180])
         import("flap.dxf");
         }
         charPreview(c1);
     }
    charPreview(c1); 
}

module flap(c1,c2,c3, part){
    //print flaps with character cutout
    if (part==99){
     difference(){ 
     union(){
     color("black")
     linear_extrude(h=(layers*layerheight))
     import("flap.dxf");
     
     color("black")
     linear_extrude(h=(layers*layerheight))
     rotate([0,0,180])
     import("flap.dxf");
     }
     char1(c1);
     char2(c2);
     char3(c3);
     }
    }

    //print just the characters
    if (colorLayer[c1] == part) { char1(c1); }
    if (colorLayer[c2] == part) { char2(c2); }
    if (colorLayer[c3] == part) { char3(c3); }

}

module charPreview(c){
difference(){
     color(colors[colorLayer[c]])
     translate([0,charYposOffset[c],layerheight*(layers-1)])
     linear_extrude(h=layerheight)
     text(chars[c], size=fontsize+charSizeOffset[c], font=fonts[charFont[c]], halign="center", valign="center");
     
     translate([-20,-0.25,layerheight*(layers-1)])
     cube([50,0.5,layerheight]);
}
}

module char1(c){
 difference(){
     color(colors[colorLayer[c]])
     translate([0,-charYposOffset[c],0])
     linear_extrude(h=layerheight)
     rotate([180,0,0])
     text(chars[c], size=fontsize+charSizeOffset[c], font=fonts[charFont[c]], halign="center", valign="center");
     
     translate([-20,-0.25,0])
     cube([50,20,layerheight]);
 }
}

module char2(c){
difference(){
     color(colors[colorLayer[c]])
     translate([0,charYposOffset[c],layerheight*(layers-1)])
     linear_extrude(h=layerheight)
     text(chars[c], size=fontsize+charSizeOffset[c], font=fonts[charFont[c]], halign="center", valign="center");
     
     translate([-20,-0.25,layerheight*(layers-1)])
     cube([50,0.5,layerheight]);
     
}
}

module char3(c){
 difference(){
     color(colors[colorLayer[c]])
     translate([0,-charYposOffset[c],0])
     linear_extrude(h=layerheight)
     rotate([180,0,0])
     text(chars[c], size=fontsize+charSizeOffset[c], font=fonts[charFont[c]], halign="center", valign="center");
     
     translate([-20,-20+0.25,0])
     cube([50,20,layerheight]);
 }
}




