# Starting a building model from nothing

Use this when the design keeps no building model (no `.ifc` file) and the person has none to upload: a house or a
shop they are designing from an idea. You start the model yourself. You write it as text, from the starting model
below, and keep it in the design with the file tool `save_a_design_file`. The building-model helper then opens it like
any other model. Rooms, walls, doors and shapes come after, with the helper's own editing steps, and each edited copy is
saved as a new file.

Do not improvise IFC text. Copy the starting model below, change only the values this page names, and keep every line's
form exactly. The starting model passes IfcOpenShell's schema check with its rules (`ifc_validate` with
`express_rules: true`) and buildingSMART's validation rules (`ifc_check_rules`) with no errors and no warnings. flo2-ifc's
tests check that on every change, with every variant shown here.

## Ask the person first

- **Units**: feet or metres. Every length you write later is in this unit: storey heights, placements, sizes.
- **Where**: the site's latitude and longitude, in decimal degrees (a pin on a map gives them, like `40.748440,
  -73.985664`). Also its height above sea level, if they know it.
- **Names**: the project, the building, and each storey (floor) with its height above the first floor.
- **A map position**, if they have one: the site's coordinates on a map grid, with the grid's name. A survey or a site
  plan often gives them. See "The map projection", below.

## The starting model

This is a two-storey building in feet. Its site is at latitude 40.748440 and longitude -73.985664, an example place
(change it). Write the file exactly like this, one entity per line, with no comments.

```ifc
ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('ViewDefinition [DesignTransferView]'),'2;1');
FILE_NAME('example-studio.ifc','2026-10-24T09:00:00',(''),(''),'','flo2 starting model','');
FILE_SCHEMA(('IFC4'));
ENDSEC;
DATA;
#1=IFCPROJECT('3G4PWt$JjC_efFDzBUr55P',$,'Example studio',$,$,$,$,(#30),#10);
#10=IFCUNITASSIGNMENT((#13,#16,#19,#22));
#11=IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.);
#12=IFCMEASUREWITHUNIT(IFCLENGTHMEASURE(0.3048),#11);
#13=IFCCONVERSIONBASEDUNIT(#24,.LENGTHUNIT.,'foot',#12);
#14=IFCSIUNIT(*,.AREAUNIT.,$,.SQUARE_METRE.);
#15=IFCMEASUREWITHUNIT(IFCAREAMEASURE(0.09290304),#14);
#16=IFCCONVERSIONBASEDUNIT(#25,.AREAUNIT.,'square foot',#15);
#17=IFCSIUNIT(*,.VOLUMEUNIT.,$,.CUBIC_METRE.);
#18=IFCMEASUREWITHUNIT(IFCVOLUMEMEASURE(0.028316846592),#17);
#19=IFCCONVERSIONBASEDUNIT(#26,.VOLUMEUNIT.,'cubic foot',#18);
#20=IFCSIUNIT(*,.PLANEANGLEUNIT.,$,.RADIAN.);
#21=IFCMEASUREWITHUNIT(IFCPLANEANGLEMEASURE(0.017453292519943295),#20);
#22=IFCCONVERSIONBASEDUNIT(#27,.PLANEANGLEUNIT.,'degree',#21);
#24=IFCDIMENSIONALEXPONENTS(1,0,0,0,0,0,0);
#25=IFCDIMENSIONALEXPONENTS(2,0,0,0,0,0,0);
#26=IFCDIMENSIONALEXPONENTS(3,0,0,0,0,0,0);
#27=IFCDIMENSIONALEXPONENTS(0,0,0,0,0,0,0);
#30=IFCGEOMETRICREPRESENTATIONCONTEXT($,'Model',3,1.E-05,#33,#34);
#31=IFCGEOMETRICREPRESENTATIONSUBCONTEXT('Body','Model',*,*,*,*,#30,$,.MODEL_VIEW.,$);
#32=IFCGEOMETRICREPRESENTATIONSUBCONTEXT('Axis','Model',*,*,*,*,#30,$,.GRAPH_VIEW.,$);
#33=IFCAXIS2PLACEMENT3D(#35,$,$);
#34=IFCDIRECTION((0.,1.));
#35=IFCCARTESIANPOINT((0.,0.,0.));
#40=IFCPROJECTEDCRS('EPSG:32618','WGS 84 / UTM zone 18N',$,$,$,$,#11);
#41=IFCMAPCONVERSION(#30,#40,585631.397,4511326.923,0.,1.,0.,0.3048);
#50=IFCSITE('1ROIFaQrTFHBttK6Ey_hFx',$,'Site',$,$,#51,$,$,.ELEMENT.,(40,44,54,384000),(-73,-59,-8,-390400),0.,$,$);
#51=IFCLOCALPLACEMENT($,#52);
#52=IFCAXIS2PLACEMENT3D(#35,$,$);
#60=IFCBUILDING('1YyEDFKvr8cv_ZFIs1awSP',$,'Example studio',$,$,#61,$,$,.ELEMENT.,$,$,$);
#61=IFCLOCALPLACEMENT(#51,#62);
#62=IFCAXIS2PLACEMENT3D(#35,$,$);
#70=IFCBUILDINGSTOREY('3xegXjyQX6ROIZbgfd6iRY',$,'Level 1',$,$,#71,$,$,.ELEMENT.,0.);
#71=IFCLOCALPLACEMENT(#61,#72);
#72=IFCAXIS2PLACEMENT3D(#35,$,$);
#73=IFCBUILDINGSTOREY('0wzXh8cZ97FBN31huFxSiH',$,'Level 2',$,$,#74,$,$,.ELEMENT.,10.);
#74=IFCLOCALPLACEMENT(#61,#75);
#75=IFCAXIS2PLACEMENT3D(#76,$,$);
#76=IFCCARTESIANPOINT((0.,0.,10.));
#80=IFCRELAGGREGATES('1TA9lHRpH1wgkRt1DiW7NE',$,$,$,#1,(#50));
#81=IFCRELAGGREGATES('0IyRassUD9NA41YfZXTByV',$,$,$,#50,(#60));
#82=IFCRELAGGREGATES('1FL2CF8hT1MQ1IiQNkJdDv',$,$,$,#60,(#70,#73));
ENDSEC;
END-ISO-10303-21;
```

What it holds: a project (`#1`) in feet, square feet, cubic feet and degrees (`#10` to `#27`); its 3D context (`#30`),
with true north along +Y (`#34`) and the `Body` and `Axis` sub-contexts that shapes are added in later; a map projection
(`#40`, `#41`); a site (`#50`) on the latitude and longitude; a building (`#60`); two storeys (`#70` at 0 ft, `#73` at
10 ft); and the three relations that join them, project to site to building to storeys (`#80`, `#81`, `#82`). Each
placement is relative to the one above it: the building to the site, each storey to the building.

## Change these, and nothing else

- `FILE_NAME`: the file's name and today's date and time.
- `#1` and `#60`: the project's and the building's names. `#50`: the site's name.
- **Every GlobalId**: the 22-character text that starts `#1`, `#50`, `#60`, `#70`, `#73`, `#80`, `#81` and `#82`. Each
  must be new, and no two the same. See "GlobalIds", below. Never keep the ones shown here.
- `#50`: the latitude, the longitude (see "Latitude and longitude", below) and the height above sea level (`0.` here),
  in the model's length unit.
- `#70` and `#73`: each storey's name and its height above the first floor (the last value, `0.` and `10.`). A storey
  above the first is also placed at that height: `#76` holds the same number as `#73`'s height.
- `#40` and `#41`: the map projection, or leave both lines out. See "The map projection", below.
- To change true north: `#34` is the direction of true north in the model's plan, as (x, y). `(0.,1.)` is +Y.

Write a number with a decimal point (`10.`, `0.`, `3.5`). Keep every other line exactly as shown.

## GlobalIds

Every project, site, building, storey and relation carries a GlobalId: exactly 22 characters, each one of these 64:

`0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$`

The first character must be `0`, `1`, `2` or `3`. Every GlobalId in the file must differ from every other, and from
any in another file. To make one:

- **If you can run code**: `ifcopenshell.guid.new()` in Python; or take a random 128-bit number (a UUID) and write it in
  base 64 with the alphabet above, the first character from its top 2 bits and the other 21 from 6 bits each.
- **Otherwise**: choose the first character from `0123`, then 21 characters at random from the 64 above. Do not copy an
  id from an example, and do not count up (`...001`, `...002`): random ones do not collide.

A GlobalId of the wrong length, a character outside the 64, or a first character above `3` fails buildingSMART's rule
PJS003. A repeated one fails `ifc_validate`.

## Latitude and longitude

IFC writes a latitude or longitude as four whole numbers: degrees, minutes, seconds and millionths of a second. North
and east are positive. **South and west are negative, in every one of the four numbers.** From decimal degrees:

1. Take the value without its sign, and multiply it by 3,600,000,000. Round to a whole number, N: the angle in millionths
   of a second.
2. Degrees = N divided by 3,600,000,000, whole part. Take that many times 3,600,000,000 off N.
3. Minutes = what is left divided by 60,000,000, whole part. Take that off.
4. Seconds = what is left divided by 1,000,000, whole part. Millionths = what is left.
5. If the value was negative (south or west), put a minus on all four.

Worked, for the starting model's site:

- Latitude 40.748440: N = 146,694,384,000. 40 degrees leaves 2,694,384,000; 44 minutes leaves 54,384,000; 54 seconds
  leaves 384,000. So `(40,44,54,384000)`.
- Longitude -73.985664: without the sign, N = 266,348,390,400. 73 degrees leaves 3,548,390,400; 59 minutes leaves
  8,390,400; 8 seconds leaves 390,400. West, so `(-73,-59,-8,-390400)`.
- In the southern and eastern hemispheres, for example latitude -33.856784 and longitude 151.215297:
  `(-33,-51,-24,-422400)` and `(151,12,55,69200)`.

The calc helper does this arithmetic exactly, if you would rather not do it by hand.

## In metres

For a model in metres, put these lines in place of the lines `#10` to `#27`, and in `#41` make the last value (the
scale) `1.` instead of `0.3048`. Every length is then in metres: a storey 3 m up is `3.` in its storey line and in its
placement point.

```ifc
#10=IFCUNITASSIGNMENT((#11,#14,#17,#22));
#11=IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.);
#14=IFCSIUNIT(*,.AREAUNIT.,$,.SQUARE_METRE.);
#17=IFCSIUNIT(*,.VOLUMEUNIT.,$,.CUBIC_METRE.);
#20=IFCSIUNIT(*,.PLANEANGLEUNIT.,$,.RADIAN.);
#21=IFCMEASUREWITHUNIT(IFCPLANEANGLEMEASURE(0.017453292519943295),#20);
#22=IFCCONVERSIONBASEDUNIT(#27,.PLANEANGLEUNIT.,'degree',#21);
#27=IFCDIMENSIONALEXPONENTS(0,0,0,0,0,0,0);
```

## More storeys, or one

To add a storey, add its three lines and its point, with its own new GlobalId and its height in both places, and add it
to the list in `#82`. A third storey at 20 ft:

```ifc
#90=IFCBUILDINGSTOREY('2Q8rK$7aVfB9xN3m0LcTzd',$,'Level 3',$,$,#91,$,$,.ELEMENT.,20.);
#91=IFCLOCALPLACEMENT(#61,#92);
#92=IFCAXIS2PLACEMENT3D(#93,$,$);
#93=IFCCARTESIANPOINT((0.,0.,20.));
```

and `#82` becomes `#82=IFCRELAGGREGATES('...',$,$,$,#60,(#70,#73,#90));`. For a one-storey building, leave out the
lines `#73` to `#76`, and `#82` lists `(#70)` alone.

## The map projection

buildingSMART's rules ask a model with a building in it to say where it sits on a map grid: an `IfcProjectedCRS` (the
grid) and an `IfcMapConversion` (where the model's origin is on it). Without them, `ifc_check_rules` warns **GRF003**
("There must be at least 1 instance(s) of .IfcProjectedCRS"). It is a warning, not an error; nothing else depends on it.

The starting model uses the UTM grid, which covers the whole world in metres:

- **The zone**: zone = (longitude + 180) / 6, whole part, plus 1. Longitude -73.985664 gives zone 18.
- **The grid's name** in `#40`: `EPSG:326` and the zone in two digits north of the equator (`EPSG:32618`), `EPSG:327`
  and the zone south of it (`EPSG:32756` for zone 56 south). The second value names it in words.
- **`#41`, in order**: the context (`#30`); the grid (`#40`); the **easting** and the **northing** of the model's
  origin on that grid, in metres; its height above sea level, in metres; the direction of the model's x axis on the
  grid (`1.,0.` when the model's x axis points grid east); and the **scale** from the model's unit to the grid's metres
  (`0.3048` for feet, `1.` for metres).

**The easting and the northing need a map projection, which is real computation.** Get them from the person (a survey,
a site plan, or the county's map viewer often gives them), or compute them if you can run code: in Python, with pyproj,
`Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True).transform(longitude, latitude)` gives (easting,
northing). For the starting model's site that is (585631.397, 4511326.923). Never estimate them, and never write zeros.

**If you cannot get them**, leave out the lines `#40` and `#41`, and tell the person plainly: the model is placed by
its latitude and longitude only; buildingSMART's rules will warn GRF003 until its map coordinates are added; and
everything else works. The model still passes with no errors.

If the person's survey uses another grid (a US State Plane zone, say), use that grid's EPSG code and unit: `#40`'s last
value is then that grid's length unit, and the scale in `#41` is the model's unit in that unit.

## Save it, open it, check it

1. Keep it with `save_a_design_file`: a name ending `.ifc`, and the whole text as the content.
2. Open it with the building-model helper (`use_helper_tool`, helper `building-model`, tool `ifc_tree`): it should list
   the project, the site, the building and the storeys, and say the units you chose.
3. Check it: `ifc_validate` with `express_rules: true` must say `valid: true`, and `ifc_check_rules` must report no
   errors (with no map projection, one GRF003 warning). If either finds an error, read its message, correct the line it
   names in your text, and save the file again with `base_sha256` set to the sha256 the save gave you.

Then add rooms, walls and shapes with the helper's editing steps, and save each edited copy as a new file.
