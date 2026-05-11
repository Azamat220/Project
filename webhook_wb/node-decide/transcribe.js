import ffmpeg from 'fluent-ffmpeg';
import { tmpdir } from 'os';
import { join } from 'path';
import fs from 'fs';
import { unlink } from 'fs/promises';
import OpenAI from 'openai';

const openai = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });

// Helper function to get audio duration
async function getAudioDuration(filePath) {
  return new Promise((resolve, reject) => {
    ffmpeg.ffprobe(filePath, (err, metadata) => {
      if (err) reject(err);
      else resolve(metadata.format.duration);
    });
  });
}

export async function transcribeAudio(filePath) {
  let outputPath = '';
  
  try {
    const duration = await getAudioDuration(filePath);
    outputPath = join(tmpdir(), `audio-${Date.now()}.mp3`);

    // Convert audio to MP3
    await new Promise((resolve, reject) => {
      ffmpeg(filePath)
        .toFormat('mp3')
        .on('error', reject)
        .on('end', resolve)
        .save(outputPath);
    });

    // Transcribe using OpenAI
    const transcription = await openai.audio.transcriptions.create({
      file: fs.createReadStream(outputPath),
      model: "whisper-1",
      response_format: "text"
    });

    return { text: transcription, duration };
  } catch (err) {
    console.error('❌ Transcription error:', err);
    return { text: '[Ошибка расшифровки аудио]', duration: 0 };
  } finally {
    // Cleanup temporary file if it exists
    if (outputPath) {
      try {
        await unlink(outputPath);
      } catch (cleanupErr) {
        console.error('⚠️ Cleanup error:', cleanupErr.message);
      }
    }
  }
}
