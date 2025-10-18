// Monkey-patch @strapi/admin to export unstable_tours
const Module = require('module');
const originalRequire = Module.prototype.require;

Module.prototype.require = function (id) {
  const module = originalRequire.apply(this, arguments);

  if (
    id === '@strapi/admin/strapi-admin' ||
    id.includes('@strapi/admin/dist/admin/index')
  ) {
    // Add unstable_tours as an alias to tours or a stub
    if (!module.unstable_tours && module.tours) {
      module.unstable_tours = module.tours;
    } else if (!module.unstable_tours) {
      module.unstable_tours = {};
    }
  }

  return module;
};

module.exports = {};
