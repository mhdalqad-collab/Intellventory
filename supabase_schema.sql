-- Supabase schema for persistent storage (Postgres)

create table if not exists products (
  sku_id text primary key,
  item_name text,
  category text,
  brand text,
  price numeric
);

create table if not exists bins (
  bin_id text primary key,
  floor text default 'G',
  x double precision default 0,
  y double precision default 0,
  z double precision default 0,
  zone text default 'FAST_MOVING',
  bin_capacity_units integer default 100,
  temperature_controlled boolean default false,
  display_name text default ''
);

create table if not exists entrance (
  id integer primary key generated always as identity,
  x double precision default 0,
  y double precision default 0,
  z double precision default 0
);

insert into entrance (x,y,z)
select 0,0,0
where not exists (select 1 from entrance);

create table if not exists stock_lots (
  id bigint primary key generated always as identity,
  sku_id text not null references products(sku_id) on delete cascade,
  bin_id text not null references bins(bin_id) on delete cascade,
  quantity_on_hand integer not null default 0,
  expiry_date date,
  received_date date default current_date
);

create index if not exists idx_stock_lots_sku on stock_lots (sku_id);
create index if not exists idx_stock_lots_bin on stock_lots (bin_id);
