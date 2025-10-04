module.exports = {module.exports = {

  routes: [  routes: [

    {    {

      method: 'POST',      method: 'POST',

      path: '/intervention',      path: '/intervention',

      handler: 'intervention.send',      handler: 'intervention.send',

    },      config: {

  ],        auth: false,

};      },

    },
  ],
};
