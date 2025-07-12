select * from phones p
order by p.date_added desc

select * from laptops l 
order by date_added DESC

select * from smartwaches s 
order by date_added DESC

select * from tablets t 
order by date_added DESC

select p.date_added, p."Product"   ,p."RAM_GB"  ,p."Storage_GB"  ,p."Rating"  from phones p 
where p."RAM_GB"   is null

select distinct(p.date_added) FROM phones p


select p."Brand"  ,avg(p."Price_EUR" ),count(p."Brand") as BrandsSum, avg(p."Rating" ) as rating , sum(p."Reviews" ) as reviews
from phones p
group by "Brand"  
order by count("Brand" ) desc

select count(*) 
from phones
where "Battery_Info" is null


select p."Brand"  ,p."RAM_GB"  ,p."Storage_GB" ,avg(p."Price_EUR" ) from phones p
where p."Storage_GB" >=64 and p."RAM_GB"  >=8
group by p."Brand" ,p."Storage_GB" ,p."RAM_GB" 
order by avg(p."Price_EUR" ) desc
limit 10

select count(*) from smartwaches  
where date_added ='2025-06-19'

select distinct(p.date_added ),count(*) from phones p
group by distinct(p.date_added)

select count(*) from phones p 
where p."Installments_in_total" = 0


select p."Brand"  ,avg(p."Price_EUR" ),count(p."Brand" ) as BrandsSum, avg(p."Rating" ) as rating , sum(p."Reviews" ) as reviews
from phones p
group by p."Brand" 
order by count(p."Brand" ) desc


select l.date_added ,count(* ) as Sum_of_nulls from laptops l
where l."Rating" is  null
group by l.date_added 
order by date_added 







