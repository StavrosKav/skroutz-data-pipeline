"""Bypass attempts against the /ask SQL guard (audit 29 Oct 2026)."""

import pytest

from agents.ask.guard import SQLGuardError, validate_sql

BYPASSES = [
    # SQL hidden in a string and executed by an XML helper
    "select query_to_xml('select usename,passwd from pg_shadow',true,true,'')",
    "select query_to_xml_and_xmlschema('select 1',true,true,'')",
    "select table_to_xml('pg_authid',true,true,'')",
    "select cursor_to_xml('c',1,true,true,'')",
    "select database_to_xml(true,true,'')",
    "select xpath('/a', '<a/>')",
    "select pg_catalog.query_to_xml('select 1',true,true,'')",
    'select "query_to_xml"(\'select 1\',true,true,\'\')',
    "select query_to_xml /* c */ ('select 1',true,true,'')",
    # file and server functions
    "select pg_ls_waldir()",
    "select pg_ls_logdir()",
    "select pg_read_file('postgresql.conf')",
    "select pg_read_binary_file('x')",
    "select pg_stat_file('x')",
    "select lo_import('x')",
    "select dblink('host=x','select 1')",
    "select set_config('a','b',false)",
    "select pg_sleep(10)",
    "select pg_catalog.pg_sleep(10)",
    "select current_setting('data_directory')",
    "select inet_server_addr()",
    "select version()",
    "select pg_backend_pid()",
    # string tricks
    "select * from products where product_name = U&'\\0041'",
    "select * from products where product_name = E'\\x41'",
    "select $$select 1$$",
    "select $q$x$q$",
    # SQL keywords smuggled inside a literal
    "select 'select * from pg_shadow' as x from products",
    # other catalogs and schemas
    "select * from pg_catalog.pg_user",
    "select * from information_schema.tables",
    "select * from products, pg_shadow",
    "select (select passwd from pg_shadow limit 1) from products",
    # multiple statements and writes
    "select 1; drop table products",
    "copy products to '/tmp/x'",
    "with x as (delete from products returning *) select * from x",
]


@pytest.mark.parametrize("sql", BYPASSES)
def test_bypass_rejected(sql):
    with pytest.raises(SQLGuardError):
        validate_sql(sql)


OK = [
    "select product_name, price_eur from mv_latest_prices where category='phone' order by price_eur limit 5",
    "select brand, count(*) from products where product_name ilike '%galaxy%' group by brand",
    "with t as (select * from products) select count(*) from t",
    "select p.brand from products p join price_snapshots s on s.product_id = p.id limit 3",
    "select product_name from products where product_name ilike '%Watch (2024)%'",
    "select round(avg(price_eur), 2), date_trunc('month', date) from price_snapshots group by 2",
]


@pytest.mark.parametrize("sql", OK)
def test_normal_queries_still_pass(sql):
    validate_sql(sql)
