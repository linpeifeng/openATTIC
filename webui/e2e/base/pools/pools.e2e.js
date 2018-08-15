"use strict";

var helpers = require("../../common.js");

describe("Pools panel", function () {

  beforeAll(function () {
    helpers.login();
    element(by.css("ul .tc_menuitem_pools > a")).click();
    element(by.css(".tc_entries_dropdown")).click();
    element(by.css(".tc_entries_100")).click();
  });

  it("should show the oadatatable", function () {
    expect(element(by.css(".tc_oadatatable_pools")).isDisplayed()).toBe(true);
    browser.sleep(400);
  });

  /* Not implemented yet!
  it('should have an add button', function(){
    expect(element(by.css('.tc_addPoolBtn')).isDisplayed()).toBe(true);
    browser.sleep(400);
  });

  it('should have a delete button', function(){
    element(by.css('.tc_menudropdown')).click();
    browser.sleep(400);
    expect(element(by.css('.tc_deletePoolBtn2')).isDisplayed()).toBe(true);
    element(by.css('.tc_menudropdown')).click();
    browser.sleep(400);
  });

  it('should switch to delete button when selecting a pool', function(){
    for(var key in helpers.configs.pools){
      var pool = helpers.configs.pools[key];
      element.all(by.cssContainingText('td', pool.name)).get(0).click();
      browser.sleep(400);
      expect(element(by.css('.tc_deletePoolBtn')).isDisplayed()).toBe(true);

      break;
    }
  });
  */

  it("should display the configured pools", function () {
    for (var key in helpers.configs.pools) {
      var pool = helpers.configs.pools[key];
      element.all(by.cssContainingText("option", pool.name))
        .then(function findMatch (pname) {
          let exactPool = undefined;
          if (pool.name === pname) {
            exactPool = pname;
            return true;
          }

          if (exactPool) {
            expect(element(by.cssContainingText("td", exactPool)).isDisplayed()).toBe(true);
          }
        });
    }
  });

  afterAll(function () {
    console.log("pools -> pools.e2e.js");
  });
});
