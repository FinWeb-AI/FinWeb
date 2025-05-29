document.addEventListener('DOMContentLoaded', () => {
  if (!window.tsParticles) return console.error('tsParticles 沒載到！');

  tsParticles.load('tsparticles', {
    fullScreen:{ enable:false },
    background:{ color:'transparent' },
    detectRetina:true,
    fpsLimit:60,

    emitters:{
      position:{ x:50, y:50 },
      rate:{ delay:0.08, quantity:6 },
      life:{ count:0 }
    },

    particles:{
      number:{ value:0 },
      life:{ duration:{ value:10 } },

      size:{ value:{ min:2, max:4 } },

      color:{
        value:{ h:0, s:100, l:60 },
        animation:{ enable:true, speed:80 }
      },

      opacity:{
        value:1,
        animation:{
          enable:true,
          speed:2,
          startValue:'max',
          destroy:'min'
        }
      },

      move:{
        enable:true,
        speed:{ min:1, max:3 },
        outModes:{ default:'destroy' },
        gravity:{
          enable:true,
          acceleration:0.25,
          inverse:false,
          maxSpeed:5
        },
        attract:{
          enable:true,
          distance:240,
          rotate:{ x:3000, y:3000 }
        }
      },

      orbit:{
        enable:true,
        radius:{ min:80, max:260 },
        animation:{ enable:true, speed:2 }
      },

      shape:{ type:'circle' },
      links:{ enable:false },
      collisions:{ enable:false }
    }
  }).then(()=>console.log('tsParticles 雲團啟動'));
});
