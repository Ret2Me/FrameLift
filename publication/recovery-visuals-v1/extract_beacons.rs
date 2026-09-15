//! Figure-only parser for the pinned CANVAS 264-byte beacon layout.
//! No demodulation, bit repair, unit guessing or interpolation is performed here.
use std::io::{self, BufRead};

fn crc16_x25(data: &[u8]) -> u16 {
    let mut c = 0xffffu16;
    for &b in data { c ^= b as u16; for _ in 0..8 { c = if c & 1 != 0 {(c >> 1) ^ 0x8408} else {c >> 1}; } }
    !c
}
fn unhex(s: &str) -> Result<Vec<u8>, String> {
    if s.len()%2 != 0 {return Err("odd hex length".into());}
    (0..s.len()).step_by(2).map(|i|u8::from_str_radix(&s[i..i+2],16).map_err(|_|"invalid hex".into())).collect()
}
fn u16be(b:&[u8], i:usize)->u16 {u16::from_be_bytes([b[i],b[i+1]])}
fn u32be(b:&[u8], i:usize)->u32 {u32::from_be_bytes([b[i],b[i+1],b[i+2],b[i+3]])}
fn beacon_pdu(frame:&[u8])->Result<Option<&[u8]>,String>{
    if frame.len()<2{return Err("missing received FCS".into());}
    let (b,fcs)=frame.split_at(frame.len()-2);
    if crc16_x25(b)!=u16::from_le_bytes([fcs[0],fcs[1]]){return Err("received FCS mismatch".into());}
    if b.len()!=264{return Ok(None);}
    let callsign=|offset:usize|b[offset..offset+6].iter().map(|x|(x>>1) as char).collect::<String>();
    if callsign(0)!="CANVAS"||callsign(7)!="LASP  "||b[6]&1!=0||b[13]&1!=1||b[14]!=3||b[15]!=0xf0 {
        return Err("unexpected AX.25 beacon envelope".into());
    }
    let h=u16be(b,16);
    if h&0x3ff!=0x20{return Ok(None);}
    if h>>13!=0 || h&0x1000!=0 || h&0x0800==0 || u16be(b,18)>>14!=3 || (u16be(b,20) as usize)+7!=b.len()-16 {
        return Err("invalid/unhandled beacon space-packet header".into());
    }
    Ok(Some(b))
}
fn main()->Result<(),Box<dyn std::error::Error>>{
    println!("pdu_hex,sequence_count,stored_flag,packet_seconds,met_seconds,battery1_temp_raw,solar_panel1_temp_raw,battery_soc_raw");
    let mut accepted=0;let mut other=0;
    for line in io::stdin().lock().lines(){let line=line?;let frame=unhex(line.trim())?;match beacon_pdu(&frame)?{
      Some(b)=>{let hex=b.iter().map(|x|format!("{x:02x}")).collect::<String>();println!("{},{},{},{},{},{},{},{}",hex,u16be(b,18)&0x3fff,(u16be(b,16)>>10)&1,u32be(b,22),u32be(b,44),u16be(b,98),u16be(b,104),u16be(b,146));accepted+=1;}
      None=>other+=1,
    }}
    eprintln!("Verified received FCS for all frames; {accepted} CANVAS beacons; {other} other packet types excluded from sensor plots.");
    Ok(())
}
#[cfg(test)]mod tests{
 use super::*;
 #[test]fn standard_crc_check(){assert_eq!(crc16_x25(b"123456789"),0x906e);}
 #[test]fn malformed_hex_rejected(){assert!(unhex("0").is_err());assert!(unhex("xz").is_err());}
 #[test]fn corrupt_fcs_rejected(){assert!(beacon_pdu(&[1,2,3,4]).is_err());}
 #[test]fn valid_other_packet_not_misparsed(){let mut b=vec![0u8;44];b.extend(crc16_x25(&b).to_le_bytes());assert!(beacon_pdu(&b).unwrap().is_none());}
 #[test]fn known_big_endian_offsets(){let b=[0x12,0x34,0x56,0x78];assert_eq!(u16be(&b,0),0x1234);assert_eq!(u32be(&b,0),0x12345678);}
}
